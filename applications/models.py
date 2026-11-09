from django.db import models
class Application(models.Model):
    STATUS=[(x,x) for x in ('discovered','qualified','prepared','submitted','skipped','blocked','failed','needs_review')]
    job=models.ForeignKey('jobs.Job',on_delete=models.PROTECT,related_name='applications')
    candidate=models.ForeignKey('profiles.CandidateProfile',on_delete=models.PROTECT)
    status=models.CharField(max_length=20,choices=STATUS,default='discovered')
    attempted_at=models.DateTimeField(null=True,blank=True)
    submitted_at=models.DateTimeField(null=True,blank=True)
    confirmation_reference=models.CharField(max_length=500,blank=True)
    confirmation_evidence=models.FileField(upload_to='private/evidence/',blank=True)
    notes=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['job','candidate'],name='one_application_per_candidate_job')]
    def __str__(self): return f'{self.candidate}: {self.job} ({self.status})'
