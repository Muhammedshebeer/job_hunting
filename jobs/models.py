from django.db import models
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
    class Meta:
        ordering=['-posted_at']
        constraints=[models.UniqueConstraint(fields=['source','source_id'], name='unique_source_job_id', condition=~models.Q(source_id=''))]
    def __str__(self): return f'{self.company} — {self.title}'
