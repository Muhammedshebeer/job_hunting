from django.db import models
class CandidateProfile(models.Model):
    name = models.CharField(max_length=200)
    resume = models.FileField(upload_to='private/resumes/')
    resume_text = models.TextField(blank=True)
    notice_days = models.PositiveSmallIntegerField(default=60)
    employment_visa = models.BooleanField(default=True)
    needs_transfer = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    def __str__(self): return self.name
