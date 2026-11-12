from jobs import qualification as policy
from django.utils import timezone
def eligible(job, profile=None):
    if profile is not None:
        from applications.services.preparation import job_assessment
        assessment = job_assessment(job, profile)
        return (assessment['status'] == 'eligible',
                '; '.join(assessment['reasons']) or 'Eligible for application preparation')
    if job.requires_review: return False, 'Discovery requires review'
    from jobs.services.discovery import ROLE_PATTERN
    from applications.services.preparation import application_source_issues
    if not ROLE_PATTERN.search(job.title) or not job.location.strip(): return False, 'Role or verified location does not qualify'
    if application_source_issues(job): return False, 'Application source evidence requires review'
    now=timezone.now()
    if not policy.recent_publication(job.posted_at, now): return False, f'Outside verified last {policy.PUBLICATION_WINDOW_DAYS} days'
    if not job.accepting_applications: return False, 'Closed'
    if not job.company or not job.title or not job.description: return False, 'Vacancy details unverified'
    from jobs.services.job_alerts import parse_publication
    expires = parse_publication(job.discovery_metadata.get('valid_through'))
    if expires and expires < now: return False, 'Expired'
    if job.match_score < policy.MIN_CV_SCORE: return False, f'Match below {policy.MIN_CV_SCORE}%'
    if job.min_experience is None: return False, 'Experience unverified'
    if job.min_experience is not None and job.min_experience > 3: return False, 'Minimum experience above 3 years'
    if job.max_experience is not None and job.max_experience < 1: return False, 'Maximum experience below 1 year'
    return True, 'Eligible for application preparation'
