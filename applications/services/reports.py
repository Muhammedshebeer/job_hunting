"""Durable daily outbox. Never automatically retry an ambiguous SMTP handoff."""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from email.utils import make_msgid

from applications.email_backend import REPORT_RECIPIENT, load_delivery_config
from applications.models import Application, DailyReport
from applications.services.budget import uae_day, day_bounds
from applications.services.preparation import job_assessment, public_application_url
from applications.services.routes import application_category
from applications.services.supervised import current_approval_issues
from jobs.models import DiscoveryRun, Job
from jobs.services.discovery_lock import discovery_execution_lock, check_discovery_ownership
from jobs.services.discovery_runs import summary_counts
from jobs import qualification as policy
from jobs.services.locations import prioritize_jobs, location_group, LOCATION_GROUPS


def submission_counts(profile, day=None):
    """Confirmed submissions for a Dubai day, independent of job search filters."""
    day = day or uae_day()
    start, end = day_bounds(day)
    apps = Application.objects.filter(candidate=profile, status='submitted',
        submitted_at__gte=start, submitted_at__lt=end).select_related('job').order_by('pk')
    from applications.services.routes import confirmed_submission
    companies = {}
    submitted = 0
    for app in apps:
        if not confirmed_submission(app):
            continue
        submitted += 1
        name = (app.company_snapshot or app.job.company).strip() or 'Company unverified'
        key = name.casefold()
        entry = companies.setdefault(key, {'name': name, 'count': 0})
        entry['count'] += 1
    # There is no automatic submission implementation. Never infer auto success
    # from a draft, approval, external opening, or an unattributed legacy record.
    return {'day': day, 'auto_applied': 0, 'auto_applied_companies': [],
            'confirmed_submissions': submitted,
            'confirmed_company_count': len(companies),
            'confirmed_companies': [companies[key] for key in sorted(companies)]}


def workflow_rows(profile):
    apps = {app.job_id: app for app in Application.objects.filter(candidate=profile).select_related('job', 'candidate')}
    rows = []
    for job in prioritize_jobs(Job.objects.order_by('-discovered_at')):
        app = apps.get(job.pk)
        assessment = job_assessment(job, profile)
        category = application_category(job, profile, app)
        rows.append({'job': job, 'app': app, 'score': assessment['score'], 'category': category,
            'location_group': location_group(job.location),
            'qualified': assessment['status'] == 'eligible',
            'url': public_application_url(job),
            'status': app.get_status_display() if app else assessment['status'].replace('_', ' ').title(),
            'approved': bool(app and app.status in ('approved', 'externally_opened') and not current_approval_issues(app))})
    return rows


def latest_discovery_snapshot(profile):
    runs = DiscoveryRun.objects.filter(candidate=profile)
    return (runs.filter(status='completed').order_by('-finished_at', '-pk').first()
            or runs.filter(status='failed').order_by('-finished_at', '-pk').first())


def report_context(report):
    rows = workflow_rows(report.candidate)
    review_companies = {}
    for row in rows:
        if row['category'] != 'needs_review' and not (
                row['category'] in ('manual_apply', 'auto_apply') and not row['approved']):
            continue
        name = row['job'].company.strip() or 'Company unverified'
        entry = review_companies.setdefault(name.casefold(), {'name': name, 'count': 0})
        entry['count'] += 1
    start, end = day_bounds(report.report_date)
    success = [r for r in rows if r['category'] == 'submitted' and start <= r['app'].submitted_at < end]
    failed = [r for r in rows if r['category'] == 'failed' and start <= r['app'].updated_at < end]
    runs = DiscoveryRun.objects.filter(candidate=report.candidate)
    latest = report.discovery_run or latest_discovery_snapshot(report.candidate)
    if latest is None:
        note = 'No fully completed discovery run is available. No discovery success is implied.'
    elif latest.status != 'completed':
        note = 'No fully completed snapshot selected. Showing a failed/partial run, not a successful discovery.'
    elif latest.scheduled_for != report.report_date:
        note = 'Using the latest fully completed discovery run from an earlier or unscheduled run, not a completed scheduled run for this report date. Vacancy statuses are recalculated when this email is prepared.'
    else:
        note = 'Using the fully completed scheduled discovery for this UAE report date. Vacancy statuses are recalculated when this email is prepared.'
    active = runs.filter(status__in=('queued', 'running')).first()
    if active:
        note += ' A discovery request is queued/running; its partial counts are not included in this snapshot.'
    attempt = runs.first()
    if attempt and attempt.status == 'failed' and (latest is None or attempt.pk != latest.pk):
        note += f' The most recent discovery attempt #{attempt.pk} failed; its partial counts are excluded from the selected completed snapshot.'
    return {'report': report, 'sections': [
        ('Successfully submitted applications', success),
        ('Approved applications awaiting submission (authorized integration)', [r for r in rows if r['approved'] and r['category'] == 'auto_apply']),
        ('Manual applications required', [r for r in rows if r['category'] == 'manual_apply' and r['approved']]),
        ('Pending approval', [r for r in rows if r['category'] in ('manual_apply', 'auto_apply') and not r['approved']]),
        ('Applications needing clarification', [r for r in rows if r['category'] == 'needs_review']),
        ('Failed submissions', failed)],
        'discovery': latest, 'discovery_note': note, 'summary': summary_counts(latest.totals if latest else {}),
        'eligible_count': sum(r['category'] in ('auto_apply', 'manual_apply') for r in rows),
        'qualified_count': sum(r['qualified'] for r in rows), 'policy': policy.snapshot(),
        'submission_counts': submission_counts(report.candidate, report.report_date),
        'review_companies': [review_companies[key] for key in sorted(review_companies)],
        'review_job_count': sum(company['count'] for company in review_companies.values()),
        'location_sections': [(label, [r for r in rows if r['location_group'] == code and r['qualified']])
                              for code, label in LOCATION_GROUPS],
        'test_email': report.kind == 'test'}


def queue_report(profile, *, discovery_run=None, kind='daily', retry_failed=False):
    if kind == 'test':
        return DailyReport.objects.create(candidate=profile, recipient=REPORT_RECIPIENT,
            report_date=uae_day(), kind='test'), True
    with transaction.atomic():
        report, created = DailyReport.objects.get_or_create(recipient=REPORT_RECIPIENT, report_date=uae_day(), kind='daily',
            defaults={'candidate': profile, 'discovery_run': discovery_run})
        if retry_failed and report.status == 'failed':
            updated = DailyReport.objects.filter(pk=report.pk, status='failed').update(status='queued', error_summary='')
            created = bool(updated)
        return report, created


def _deliver(report):
    if report.status != 'queued':
        return report
    try:
        if report.recipient != REPORT_RECIPIENT or not report.candidate.active:
            raise ValueError('Report recipient or candidate is not authorized')
        config = load_delivery_config()
        context = report_context(report)
        html = render_to_string('applications/email/daily_report.html', context)
        text = render_to_string('applications/email/daily_report.txt', context)
        backend = get_connection(backend=getattr(settings, 'REPORT_EMAIL_BACKEND',
            'applications.email_backend.CredentialSMTPBackend'))
        message_id = make_msgid(domain='uae-job-agent.local')
        mail = EmailMultiAlternatives(
            subject=f"{'TEST - ' if report.kind == 'test' else ''}UAE Job Agent daily report - {report.report_date.isoformat()}",
            body=text, from_email=config['from_email'], to=[report.recipient], connection=backend,
            headers={'Message-ID': message_id})
        mail.attach_alternative(html, 'text/html')
    except Exception:
        report.status = 'failed'
        report.error_summary = 'Email could not be prepared securely. No SMTP send was attempted. Check configuration and retry explicitly.'
        report.save(update_fields=['status', 'error_summary'])
        return report
    # Commit this state BEFORE talking to SMTP. An interrupted send cannot be retried blindly.
    check_discovery_ownership()
    claimed = DailyReport.objects.filter(pk=report.pk, status='queued').update(status='sending', message_id=message_id)
    if not claimed:
        report.refresh_from_db()
        return report
    check_discovery_ownership()
    try:
        accepted = mail.send(fail_silently=False)
        if accepted != 1:
            raise RuntimeError('SMTP acceptance unavailable')
    except Exception:
        report.status = 'delivery_unknown'
        report.error_summary = 'SMTP handoff failed or its outcome is unknown. Automatic resend is blocked to prevent duplicates. Check the provider using the Message-ID.'
    else:
        report.status, report.sent_at, report.error_summary = 'sent', timezone.now(), ''
    check_discovery_ownership()
    report.save(update_fields=['status', 'sent_at', 'error_summary'])
    return report


def process_report_queue(report_id=None):
    with discovery_execution_lock():
        # Owning the shared session lock proves any previous sending worker is gone.
        DailyReport.objects.filter(status='sending').update(status='delivery_unknown',
            error_summary='The previous worker stopped during SMTP handoff. Automatic resend is blocked; check the Message-ID with your provider.')
        reports = DailyReport.objects.filter(status='queued').order_by('queued_at', 'pk')
        if report_id is not None:
            reports = reports.filter(pk=report_id)
        report = reports.first()
        return _deliver(report) if report else None
