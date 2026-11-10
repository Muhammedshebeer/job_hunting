"""No live automatic submission integration is authorized or enabled.

Discovery API permission does not authorize submission. A future adapter needs
documented employer permission, a separate per-application submission approval,
budget reservation, idempotency, and verified confirmation evidence.
"""
from applications.services.preparation import approval_blockers, job_assessment, synthetic_vacancy, cv_file_digest
from applications.services.supervised import current_approval_issues


def authorized_adapter_for(job):
    return None


def application_category(job, profile, app=None):
    if synthetic_vacancy(job):
        return 'excluded'
    if app and app.status == 'submitted':
        return 'submitted' if confirmed_submission(app) else 'needs_review'
    if app and app.status == 'failed':
        return 'failed'
    if app and app.status == 'skipped':
        return 'excluded'
    assessment = job_assessment(job, profile)
    if assessment['status'] == 'rejected':
        return 'excluded'
    if not cv_file_digest(profile):
        return 'needs_review'
    if assessment['status'] != 'eligible' or app and approval_blockers(app):
        return 'needs_review'
    if app and app.status in ('approved', 'externally_opened') and current_approval_issues(app):
        return 'needs_review'
    return 'auto_apply' if authorized_adapter_for(job) else 'manual_apply'


def confirmed_submission(app):
    if not app.submitted_at:
        return False
    if app.confirmation_reference.strip():
        return True
    try:
        return bool(app.confirmation_evidence and app.confirmation_evidence.storage.exists(app.confirmation_evidence.name))
    except OSError:
        return False
