from datetime import timedelta
from django.utils import timezone
def eligible(job):
    now=timezone.now()
    if not job.posted_at or not (now-timedelta(days=7) <= job.posted_at <= now): return False, 'Outside verified last 7 days'
    if not job.accepting_applications: return False, 'Closed'
    if job.match_score < 75: return False, 'Match below 75%'
    if job.min_experience is None and job.max_experience is None: return False, 'Experience unverified'
    if job.min_experience is not None and job.min_experience > 3: return False, 'Minimum experience above 3 years'
    if job.max_experience is not None and job.max_experience < 1: return False, 'Maximum experience below 1 year'
    return True, 'Eligible for application preparation'
