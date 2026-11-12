"""Retention of rejected-vacancy audit rows by their creation timestamp."""
from datetime import timedelta

from django.utils import timezone

from jobs.models import RejectedVacancy


REJECTED_VACANCY_RETENTION_DAYS = 5


def cleanup_rejected_vacancies(*, dry_run=False):
    cutoff = timezone.now() - timedelta(days=REJECTED_VACANCY_RETENTION_DAYS)
    expired = RejectedVacancy.objects.filter(observed_at__lt=cutoff)
    if dry_run:
        return expired.count()
    _, counts = expired.delete()
    return counts.get(RejectedVacancy._meta.label, 0)
