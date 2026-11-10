class SubmissionNotAuthorized(Exception): pass
class AuthorizedAdapter:
    # Implement only after verifying explicit authorization for the destination.
    def submit(self, job, profile):
        raise SubmissionNotAuthorized('No authorized submission adapter configured')
def confirm_submission(application_id, confirmation_reference, evidence=None, *, user=None, confirmed=False):
    # Legacy callers must now provide a human attestation and use the supervised handoff.
    if user is None or not confirmed:
        raise SubmissionNotAuthorized('Explicit staff confirmation through the supervised workflow is required')
    from applications.services.supervised import record_manual_submission
    from django.core.exceptions import ValidationError
    try:
        return record_manual_submission(application_id, user, confirmed=confirmed,
                                        reference=confirmation_reference, evidence=evidence)
    except ValidationError:
        raise SubmissionNotAuthorized('Manual confirmation failed current supervised workflow checks') from None
