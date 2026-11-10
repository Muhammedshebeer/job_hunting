import traceback
from profiles.models import CandidateProfile
from applications.services.scheduled import preview_job_report

try:
    profile = CandidateProfile.objects.get(pk=1, active=True)
    print("Active profile found:", profile.pk)

    counts, run, note = preview_job_report(
        profile,
        test_email=False
    )

    print("SUCCESS")
    print("Counts:", counts)
    print("Discovery run:", run.pk if run else None)
    print("Note:", note)

except Exception as exc:
    print("ERROR TYPE:", type(exc).__name__)
    print("ERROR:", str(exc))
    traceback.print_exc()
