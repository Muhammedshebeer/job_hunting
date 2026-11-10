from django.db import models
from django.conf import settings
class Job(models.Model):
    source = models.CharField(max_length=50)
    source_id = models.CharField(max_length=250, blank=True)
    company = models.CharField(max_length=250)
    title = models.CharField(max_length=250)
    location = models.CharField(max_length=200)
    url = models.URLField(max_length=1500, unique=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    min_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    max_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField()
    match_score = models.PositiveSmallIntegerField(default=0)
    accepting_applications = models.BooleanField(default=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    requires_review = models.BooleanField(default=False)
    review_reasons = models.JSONField(default=list, blank=True)
    discovery_metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering=['-posted_at']
        constraints=[models.UniqueConstraint(fields=['source','source_id'], name='unique_source_job_id', condition=~models.Q(source_id=''))]
    def __str__(self): return f'{self.company} — {self.title}'


class DiscoveryRun(models.Model):
    STATUS = [(value, value.title()) for value in ('queued', 'running', 'completed', 'failed')]
    candidate = models.ForeignKey('profiles.CandidateProfile', on_delete=models.PROTECT, related_name='discovery_runs')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=12, choices=STATUS, default='queued')
    slot = models.PositiveSmallIntegerField(default=1, editable=False)
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    totals = models.JSONField(default=dict, blank=True)
    source_counts = models.JSONField(default=dict, blank=True)
    source_names = models.JSONField(default=list, blank=True)
    error_summary = models.CharField(max_length=500, blank=True)
    scheduled_for = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-queued_at', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(slot=1), name='discovery_global_slot_is_one'),
            models.UniqueConstraint(fields=['slot'], condition=models.Q(status__in=['queued', 'running']),
                                    name='one_active_discovery_run'),
            models.UniqueConstraint(fields=['candidate', 'scheduled_for'], condition=models.Q(scheduled_for__isnull=False),
                                    name='one_scheduled_discovery_per_day'),
        ]

    def __str__(self):
        return f'Discovery #{self.pk} ({self.status})'


class RejectedVacancy(models.Model):
    # Audit snapshots intentionally have no Job/Application FK or application URL.
    run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name='rejected_vacancies')
    company = models.CharField(max_length=250, blank=True)
    title = models.CharField(max_length=250, blank=True)
    location = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=50)
    posted_at = models.DateTimeField(null=True, blank=True)
    rejection_code = models.CharField(max_length=64)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
