"""Human-operated browser handoff and evidence recording; never submits a form."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from applications.models import Application, ApplicationEvent
from applications.services.budget import reserve_slot
from applications.services.preparation import approval_blockers, draft_digest, same_vacancy, safe_destination
from jobs.models import Job
from profiles.models import CandidateProfile


def current_approval_issues(app):
    issues = approval_blockers(app)
    if app.status not in ('approved', 'externally_opened') or not app.approved_at or not app.approved_by_id:
        issues.append('Explicit current human approval is required.')
    if not app.approval_digest or app.approval_digest != draft_digest(app):
        issues.append('The reviewed draft approval is not current.')
    for other in Application.objects.filter(candidate_id=app.candidate_id,
            status__in=('approved', 'externally_opened', 'submitted')).exclude(pk=app.pk).select_related('job'):
        if same_vacancy(app.job, other.job):
            issues.append('Another application already represents this vacancy. Review the existing record.')
            break
    return list(dict.fromkeys(issues))


def revoke_stale_approval(app):
    """Preserve content, previous opening timestamps and all confirmation evidence."""
    if app.status in ('approved', 'externally_opened'):
        app.status = 'review_required'
        app.approved_at, app.approved_by, app.approval_digest = None, None, ''
        app.save(update_fields=['status', 'approved_at', 'approved_by', 'approval_digest', 'updated_at'])
        ApplicationEvent.objects.create(application=app, action='stale_approval_revoked')


def _locked_application(application_id):
    original = Application.objects.get(pk=application_id)
    profile = CandidateProfile.objects.select_for_update().get(pk=original.candidate_id)
    job = Job.objects.select_for_update().get(pk=original.job_id)
    app = Application.objects.select_for_update().get(pk=application_id)
    app.candidate, app.job = profile, job
    return app


def open_application(application_id, user, expected_digest):
    if not user.is_active or not user.is_staff:
        raise ValidationError('An active staff reviewer is required.')
    # Commit revocation even on a refused handoff; raising inside atomic would undo it.
    with transaction.atomic():
        app = _locked_application(application_id)
        issues = current_approval_issues(app)
        if issues:
            revoke_stale_approval(app)
            ApplicationEvent.objects.create(application=app, actor=user, action='opening_blocked')
        elif expected_digest != draft_digest(app):
            issues = ['The draft changed in another window. Reload and review it.']
        else:
            destination = safe_destination(app.application_url)
            if not destination or destination != safe_destination(app.job.url):
                issues = ['The application destination no longer matches verified source evidence.']
                revoke_stale_approval(app)
            else:
                reserve_slot(app)
                app.status = 'externally_opened'
                if not app.externally_opened_at:
                    app.externally_opened_at, app.externally_opened_by = timezone.now(), user
                app.save(update_fields=['status', 'externally_opened_at', 'externally_opened_by', 'updated_at'])
                ApplicationEvent.objects.create(application=app, actor=user, action='external_handoff_requested')
    if issues:
        raise ValidationError(issues)
    return destination


def record_manual_submission(application_id, user, *, confirmed, reference='', evidence=None):
    if not user.is_active or not user.is_staff or not confirmed:
        raise ValidationError('An active staff reviewer must explicitly confirm the completed submission.')
    reference = reference.strip()
    if not reference and evidence is None:
        raise ValidationError('A confirmation reference or confirmation evidence is required.')
    if len(reference) > 500:
        raise ValidationError('Confirmation reference is too long.')
    if evidence is not None:
        from applications.forms import validate_confirmation_file
        validate_confirmation_file(evidence)
    with transaction.atomic():
        app = _locked_application(application_id)
        if app.status == 'submitted':
            return app  # Never replace an existing reference or evidence file.
        issues = current_approval_issues(app)
        if app.status != 'externally_opened' or not app.externally_opened_at:
            issues.append('Open the approved application through the supervised workflow first.')
        if issues:
            revoke_stale_approval(app)
        else:
            if app.confirmation_reference or app.confirmation_evidence:
                raise ValidationError('Existing confirmation evidence is preserved; review this record before changing its status.')
            reserve_slot(app)
            app.status = 'submitted'
            app.submitted_at = app.submission_recorded_at = timezone.now()
            app.submission_recorded_by = user
            app.confirmation_reference = reference
            if evidence is not None:
                app.confirmation_evidence = evidence
            app.save()
            ApplicationEvent.objects.create(application=app, actor=user, action='manual_submission_confirmed')
    if issues:
        raise ValidationError(issues)
    return app


def revalidate_approvals(*, dry_run=True, profile_id=None):
    checked, invalidated = 0, 0
    queryset = Application.objects.filter(status__in=('approved', 'externally_opened'))
    if profile_id is not None:
        queryset = queryset.filter(candidate_id=profile_id)
    ids = queryset.values_list('pk', flat=True)
    for pk in list(ids):
        with transaction.atomic():
            app = _locked_application(pk)
            checked += 1
            if current_approval_issues(app):
                invalidated += 1
                if not dry_run:
                    revoke_stale_approval(app)
    return checked, invalidated
