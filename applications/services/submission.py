from django.db import transaction
from django.utils import timezone
from applications.models import Application
class SubmissionNotAuthorized(Exception): pass
class AuthorizedAdapter:
    # Implement only after verifying explicit authorization for the destination.
    def submit(self, job, profile):
        raise SubmissionNotAuthorized('No authorized submission adapter configured')
@transaction.atomic
def confirm_submission(application_id, confirmation_reference, evidence=None):
    if not confirmation_reference or not confirmation_reference.strip():
        raise ValueError('A verifiable confirmation reference is required')
    app=Application.objects.select_for_update().get(pk=application_id)
    if app.status == 'submitted': return app
    app.status='submitted'
    app.submitted_at=timezone.now()
    app.confirmation_reference=confirmation_reference.strip()
    if evidence: app.confirmation_evidence=evidence
    app.save()
    return app
