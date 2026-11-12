from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from applications.forms import PreparationForm, ReviewForm, ManualSubmissionForm
from applications.models import Application, DailyReport
from applications.services.preparation import approval_blockers, draft_digest, job_assessment, prepare_draft, review_draft, safe_destination
from jobs.models import Job, DiscoveryRun
from jobs.services.discovery_runs import enqueue_discovery, rejection_label, summary_counts, run_policy
from profiles.models import CandidateProfile
from applications.services.supervised import current_approval_issues, open_application, record_manual_submission
from applications.services.reports import workflow_rows, queue_report, submission_counts
from jobs.services.locations import prioritize_jobs, location_group, LOCATION_GROUPS


def staff_required(view):
    @login_required(login_url='/accounts/login/')
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        response = view(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return wrapped


def selected_profile(request):
    profiles = CandidateProfile.objects.filter(active=True).order_by('pk')
    value = request.GET.get('profile') or request.POST.get('profile')
    if value:
        try:
            return get_object_or_404(profiles, pk=int(value)), profiles
        except ValueError:
            raise Http404
    return profiles.first(), profiles


@staff_required
@require_GET
def dashboard(request):
    profile, profiles = selected_profile(request)
    queryset = Job.objects.all()
    query, source, selected = request.GET.get('q', '')[:200], request.GET.get('source', ''), request.GET.get('status', 'all')
    region = request.GET.get('region', 'all')
    if region not in ('all', 'uae', 'other', 'unknown'):
        region = 'all'
    if query:
        queryset = queryset.filter(Q(title__icontains=query) | Q(company__icontains=query))
    if source:
        queryset = queryset.filter(source=source)
    if profile:
        queryset = queryset.prefetch_related(Prefetch('applications',
            queryset=Application.objects.filter(candidate=profile), to_attr='candidate_applications'))
    rows, counts = [], {'all': 0, 'eligible': 0, 'needs_review': 0, 'rejected': 0, 'already_applied': 0}
    for job in prioritize_jobs(queryset.order_by('-discovered_at')):
        group = location_group(job.location)
        if region != 'all' and group != region:
            continue
        assessment = job_assessment(job, profile) if profile else {'status': 'needs_review', 'score': 0, 'reasons': ['Create an active candidate profile first.']}
        app = job.candidate_applications[0] if profile and job.candidate_applications else None
        status = 'already_applied' if app and app.status == 'submitted' else assessment['status']
        counts['all'] += 1
        counts[status] += 1
        if selected not in counts:
            selected = 'all'
        if selected == 'all' or status == selected:
            rows.append({'job': job, 'assessment': assessment, 'status': status, 'application': app,
                         'location_group': group})
    page = Paginator(rows, 20).get_page(request.GET.get('page'))
    return render(request, 'applications/dashboard.html', {'profile': profile, 'profiles': profiles,
        'page': page, 'counts': counts, 'region': region,
        'location_sections': [(label, [r for r in page if r['location_group'] == code],
            sum(r['location_group'] == code for r in rows)) for code, label in LOCATION_GROUPS],
        'selected': selected, 'query': query, 'source': source,
        'sources': Job.objects.values_list('source', flat=True).distinct().order_by('source'),
        'discovery_runs': discovery_history(profile),
        'ready_applications': ready_applications(profile),
        'workflow_sections': workflow_sections(profile),
        'submission_counts': submission_counts(profile),
        'last_discovery': DiscoveryRun.objects.filter(candidate=profile, status='completed').order_by('-finished_at').first(),
        'last_report': DailyReport.objects.filter(candidate=profile, status='sent', kind='daily').order_by('-sent_at').first(),
        'recent_reports': DailyReport.objects.filter(candidate=profile)[:5],
        'active_discovery': DiscoveryRun.objects.filter(status__in=('queued', 'running')).first()})


def workflow_sections(profile):
    rows = workflow_rows(profile) if profile else []
    return [(label, [row for row in rows if row['category'] == code]) for label, code in (
        ('Auto-Apply Eligible', 'auto_apply'), ('Manual Apply', 'manual_apply'),
        ('Submitted', 'submitted'), ('Needs Review', 'needs_review'))]


@staff_required
@require_POST
def queue_daily_report(request):
    profile, _ = selected_profile(request)
    if profile is None:
        messages.error(request, 'Select an existing active candidate first.')
    else:
        report, created = queue_report(profile)
        messages.success(request, f'Daily report #{report.pk} queued for the worker.' if created else
                         f'Daily report #{report.pk} already exists ({report.status}). A duplicate email was not queued.')
    return redirect('dashboard')


def ready_applications(profile):
    if not profile:
        return []
    rows = []
    for app in Application.objects.filter(candidate=profile, status__in=('approved', 'externally_opened')).select_related('job', 'candidate'):
        if not current_approval_issues(app):
            app.current_digest = draft_digest(app)
            rows.append(app)
    return rows


def discovery_history(profile):
    runs = list(DiscoveryRun.objects.filter(candidate=profile)[:6]) if profile else []
    for run in runs:
        run.summary = summary_counts(run.totals)
        run.policy = run_policy(run)
    return runs


def source_summaries(run):
    names = list(run.source_names)
    names.extend(name for name in run.source_counts if name not in names)
    return [{'name': name, 'counts': summary_counts(run.source_counts.get(name, {})),
             'state': ('failed' if run.source_counts.get(name, {}).get('failed_sources') else 'completed')
                      if name in run.source_counts else ('not completed' if run.status == 'failed' else 'pending')}
            for name in names]


@staff_required
@require_POST
def queue_discovery(request):
    profile, _ = selected_profile(request)
    if profile is None:
        messages.error(request, 'Select an active profile with extracted CV text first.')
        return redirect('dashboard')
    try:
        run, created = enqueue_discovery(profile, request.user)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('dashboard')
    messages.success(request, 'Discovery queued for the background worker. No applications will be submitted.'
                     if created else 'Discovery is already queued or running. Opened the active run.')
    return redirect('discovery_run_detail', pk=run.pk)


@staff_required
@require_GET
def discovery_run_detail(request, pk):
    run = get_object_or_404(DiscoveryRun.objects.select_related('candidate'), pk=pk)
    audit = run.rejected_vacancies.all()
    source, reason = request.GET.get('audit_source', ''), request.GET.get('reason', '')
    if source:
        audit = audit.filter(source=source)
    if reason:
        audit = audit.filter(rejection_code=reason)
    page = Paginator(audit, 25).get_page(request.GET.get('page'))
    for vacancy in page:
        vacancy.reason_label = rejection_label(vacancy.rejection_code, run_policy(run))
    return render(request, 'applications/discovery_run.html', {
        'run': run, 'run_policy': run_policy(run), 'summary': summary_counts(run.totals), 'source_rows': source_summaries(run),
        'audit_page': page, 'audit_source': source, 'reason': reason,
        'audit_sources': run.rejected_vacancies.values_list('source', flat=True).distinct().order_by('source'),
        'reason_options': [(code, rejection_label(code, run_policy(run))) for code in run.rejected_vacancies.values_list(
            'rejection_code', flat=True).distinct().order_by('rejection_code')],
    })


@staff_required
@require_GET
def discovery_run_status(request, pk):
    run = get_object_or_404(DiscoveryRun, pk=pk)
    return JsonResponse({'id': run.pk, 'status': run.status, 'summary': summary_counts(run.totals),
                         'sources': source_summaries(run), 'error': run.error_summary,
                         'rejected_audit_count': run.rejected_vacancies.count()})


@staff_required
@require_GET
def job_detail(request, pk):
    profile, profiles = selected_profile(request)
    job = get_object_or_404(Job, pk=pk)
    assessment = job_assessment(job, profile) if profile else None
    app = Application.objects.filter(job=job, candidate=profile).first() if profile else None
    return render(request, 'applications/job_detail.html', {'job': job, 'profile': profile,
        'assessment': assessment, 'application': app, 'preparation_form': PreparationForm(), 'destination_url': safe_destination(job.url)})


@staff_required
@require_POST
def prepare(request, pk):
    profile, _ = selected_profile(request)
    if profile is None:
        messages.error(request, 'An active candidate profile is required.')
        return redirect('dashboard')
    get_object_or_404(Job, pk=pk)
    form = PreparationForm(request.POST)
    if form.is_valid():
        try:
            app = prepare_draft(pk, profile.pk, extra_questions=form.cleaned_data['extra_questions'], user=request.user)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, 'This vacancy already has an application; opened the existing record.' if app.job_id != pk
                             else 'Draft prepared locally. Nothing was submitted and no attempt slot was reserved.')
            return redirect('application_detail', pk=app.pk)
    else:
        messages.error(request, 'Additional questions are invalid or too long.')
    return redirect(f'/jobs/{pk}/?profile={profile.pk}')


@staff_required
def application_detail(request, pk):
    app = get_object_or_404(Application.objects.select_related('job', 'candidate', 'approved_by'), pk=pk)
    if request.method not in ('GET', 'POST'):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET', 'POST'])
    form = ReviewForm(request.POST if request.method == 'POST' else None,
                      application=app, initial={'revision': draft_digest(app)})
    if request.method == 'POST' and form.is_valid():
        action = request.POST.get('action')
        if action not in ('save', 'approve', 'skip'):
            raise PermissionDenied
        try:
            app = review_draft(app.pk, request.user, resolutions=form.resolutions(), approve=action == 'approve',
                              skip=action == 'skip', confirmed=form.cleaned_data['confirmed'],
                              expected_digest=form.cleaned_data['revision'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Review saved. No application was submitted.')
            return redirect('application_detail', pk=app.pk)
    current_issues = current_approval_issues(app)
    return render(request, 'applications/application_detail.html', {'application': app,
        'form': form, 'blockers': approval_blockers(app), 'assessment': job_assessment(app.job, app.candidate),
        'can_open': not current_issues, 'current_digest': draft_digest(app),
        'manual_submission_form': ManualSubmissionForm(),
        'can_record_submission': not current_issues and app.status == 'externally_opened'})


@staff_required
@require_POST
def open_approved_application(request, pk):
    get_object_or_404(Application, pk=pk)
    try:
        destination = open_application(pk, request.user, request.POST.get('revision', ''))
    except ValidationError as exc:
        # A target=_blank POST displays this existing detail page on refusal.
        messages.error(request, ' '.join(exc.messages))
        return redirect('application_detail', pk=pk)
    response = HttpResponseRedirect(destination)
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@staff_required
@require_POST
def record_submission(request, pk):
    app = get_object_or_404(Application, pk=pk)
    form = ManualSubmissionForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            record_manual_submission(pk, request.user, confirmed=form.cleaned_data['confirmed'],
                reference=form.cleaned_data['reference'], evidence=form.cleaned_data['evidence'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Your manual submission confirmation was recorded. This agent did not submit an application.')
            return redirect('application_detail', pk=pk)
    app.refresh_from_db()
    issues = current_approval_issues(app)
    return render(request, 'applications/application_detail.html', {'application': app,
        'form': ReviewForm(application=app, initial={'revision': draft_digest(app)}),
        'blockers': approval_blockers(app), 'assessment': job_assessment(app.job, app.candidate),
        'can_open': not issues, 'current_digest': draft_digest(app), 'manual_submission_form': form,
        'can_record_submission': app.status == 'externally_opened'})


@staff_required
@require_GET
def evidence_download(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if not app.confirmation_evidence:
        raise Http404
    try:
        return FileResponse(app.confirmation_evidence.open('rb'), as_attachment=True, filename='confirmation-evidence')
    except OSError:
        raise Http404
