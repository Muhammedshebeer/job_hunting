from django.db import models
class Application(models.Model):
    STATUS=[(x,x.replace('_',' ').title()) for x in (
        'discovered','review_required','ready_for_review','approved','externally_opened','submitted','failed','skipped',
        'qualified','prepared','blocked','needs_review')]
    job=models.ForeignKey('jobs.Job',on_delete=models.PROTECT,related_name='applications')
    candidate=models.ForeignKey('profiles.CandidateProfile',on_delete=models.PROTECT)
    status=models.CharField(max_length=20,choices=STATUS,default='discovered')
    attempted_at=models.DateTimeField(null=True,blank=True)
    submitted_at=models.DateTimeField(null=True,blank=True)
    confirmation_reference=models.CharField(max_length=500,blank=True)
    confirmation_evidence=models.FileField(upload_to='private/evidence/',blank=True)
    notes=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    application_url=models.URLField(max_length=1500,blank=True)
    company_snapshot=models.CharField(max_length=250,blank=True)
    role_snapshot=models.CharField(max_length=250,blank=True)
    draft_answers=models.JSONField(default=dict,blank=True)
    cover_letter=models.TextField(blank=True)
    preparation_snapshot=models.JSONField(default=dict,blank=True)
    prepared_at=models.DateTimeField(null=True,blank=True)
    updated_at=models.DateTimeField(auto_now=True)
    approved_at=models.DateTimeField(null=True,blank=True)
    approved_by=models.ForeignKey('auth.User',null=True,blank=True,on_delete=models.SET_NULL,related_name='approved_job_drafts')
    approval_digest=models.CharField(max_length=64,blank=True)
    externally_opened_at=models.DateTimeField(null=True,blank=True)
    externally_opened_by=models.ForeignKey('auth.User',null=True,blank=True,on_delete=models.SET_NULL,related_name='externally_opened_applications')
    submission_recorded_at=models.DateTimeField(null=True,blank=True)
    submission_recorded_by=models.ForeignKey('auth.User',null=True,blank=True,on_delete=models.SET_NULL,related_name='manually_recorded_applications')
    class Meta:
        constraints=[models.UniqueConstraint(fields=['job','candidate'],name='one_application_per_candidate_job')]
    def __str__(self): return f'{self.candidate}: {self.job} ({self.status})'


class ApplicationEvent(models.Model):
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name='audit_events')
    action = models.CharField(max_length=40)
    actor = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at', '-pk']


class DailyApplicationBudget(models.Model):
    day = models.DateField(unique=True)


class ApplicationSlot(models.Model):
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name='daily_slots')
    budget = models.ForeignKey(DailyApplicationBudget, on_delete=models.PROTECT)
    reserved_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'budget'], name='one_daily_application_slot')]


class DailyReport(models.Model):
    candidate = models.ForeignKey('profiles.CandidateProfile', on_delete=models.PROTECT)
    recipient = models.EmailField()
    report_date = models.DateField()
    kind = models.CharField(max_length=8, choices=[('daily', 'Daily'), ('test', 'Test')], default='daily')
    status = models.CharField(max_length=20, choices=[(x, x.replace('_', ' ').title()) for x in
        ('queued', 'sending', 'sent', 'failed', 'delivery_unknown')], default='queued')
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_summary = models.CharField(max_length=400, blank=True)
    message_id = models.CharField(max_length=200, blank=True)
    discovery_run = models.ForeignKey('jobs.DiscoveryRun', null=True, blank=True, on_delete=models.SET_NULL)
    class Meta:
        ordering = ['-queued_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['recipient', 'report_date'],
            condition=models.Q(kind='daily'), name='one_daily_report_per_recipient')]
