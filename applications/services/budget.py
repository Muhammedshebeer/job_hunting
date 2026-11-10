"""Global 20-slot UAE-day budget; opening reserves, confirmation never infers success."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from applications.models import Application, ApplicationSlot, DailyApplicationBudget

UAE = ZoneInfo('Asia/Dubai')
DAILY_APPLICATION_LIMIT = 20


def uae_day():
    return timezone.localdate(timezone.now(), UAE)


def day_bounds(day):
    start = datetime.combine(day, time.min, tzinfo=UAE)
    return start, start + timedelta(days=1)


@transaction.atomic
def reserve_slot(app):
    day = uae_day()
    budget, _ = DailyApplicationBudget.objects.get_or_create(day=day)
    budget = DailyApplicationBudget.objects.select_for_update().get(pk=budget.pk)
    if ApplicationSlot.objects.filter(application=app, budget=budget).exists():
        return
    start, end = day_bounds(day)
    used = set(ApplicationSlot.objects.filter(budget=budget).values_list('application_id', flat=True))
    used.update(Application.objects.filter(Q(attempted_at__gte=start, attempted_at__lt=end) |
        Q(submitted_at__gte=start, submitted_at__lt=end)).values_list('pk', flat=True))
    if app.pk not in used and len(used) >= DAILY_APPLICATION_LIMIT:
        raise ValidationError('The 20-application daily limit has been reached. No new application slot is available today.')
    ApplicationSlot.objects.create(application=app, budget=budget)
