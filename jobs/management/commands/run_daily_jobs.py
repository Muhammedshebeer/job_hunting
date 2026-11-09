from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from profiles.models import CandidateProfile
from profiles.services.resume_parser import extract_pdf_text
from jobs.models import Job
from jobs.services.matching import score_job
from jobs.services.eligibility import eligible
from applications.models import Application
class Command(BaseCommand):
    help='Score imported jobs and prepare at most 20 attempts/day; NEVER submits externally'
    def add_arguments(self, parser):
        parser.add_argument('--profile-id',type=int,required=True)
        parser.add_argument('--limit',type=int,default=20)
    def handle(self,*args,**opts):
        profile=CandidateProfile.objects.get(pk=opts['profile_id'],active=True)
        if opts['limit'] < 1 or opts['limit'] > 20: raise CommandError('Limit must be 1..20')
        cv=extract_pdf_text(profile.resume.path)
        start=timezone.localdate()
        from datetime import datetime,time
        day_start=timezone.make_aware(datetime.combine(start,time.min),timezone.get_current_timezone())
        day_end=day_start+timedelta(days=1)
        used=Application.objects.filter(candidate=profile,attempted_at__gte=day_start,attempted_at__lt=day_end).count()
        remaining=max(0,opts['limit']-used)
        prepared=0
        for job in Job.objects.filter(posted_at__gte=timezone.now()-timedelta(days=7)).order_by('-posted_at'):
            if prepared>=remaining: break
            score,details=score_job(job.description,cv)
            if score!=job.match_score:
                job.match_score=score;job.save(update_fields=['match_score'])
            ok,reason=eligible(job)
            if not ok: continue
            with transaction.atomic():
                app,created=Application.objects.get_or_create(job=job,candidate=profile,defaults={'status':'prepared','notes':str(details)})
                if not created: continue
                # Reservation of an attempt slot for future authorized submission integration.
                app.attempted_at=timezone.now()
                app.save(update_fields=['attempted_at'])
                prepared+=1
        self.stdout.write(f'Prepared {prepared} applications; {used} slots previously reserved today. No external submissions made.')
