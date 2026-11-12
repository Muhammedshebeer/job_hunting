"""Shared eligibility policy. Boundary values are inclusive; no fallback dates."""
from datetime import timedelta

from django.utils import timezone

MIN_CV_SCORE = 25
PUBLICATION_WINDOW_DAYS = 3
POLICY_VERSION = 'worldwide-three-day-25-v1'


def snapshot():
    return {'version': POLICY_VERSION, 'minimum_cv_score': MIN_CV_SCORE,
            'publication_window_days': PUBLICATION_WINDOW_DAYS, 'location_scope': 'worldwide_uae_first'}


def recent_publication(posted, now):
    return bool(posted is not None and timezone.is_aware(posted) and timezone.is_aware(now)
                and now - timedelta(days=PUBLICATION_WINDOW_DAYS) <= posted <= now)


def template_policy(request):
    return {'qualification_policy': snapshot()}
