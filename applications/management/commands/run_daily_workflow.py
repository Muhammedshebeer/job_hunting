from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from profiles.models import CandidateProfile
from applications.services.daily import run_daily_workflow


class Command(BaseCommand):
    help = 'Daily authorized discovery, local drafts and report; never grants submission authorization'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--staff-id', required=True, type=int)

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Scheduled workflow requires PostgreSQL session locking.')
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        user = get_user_model().objects.filter(pk=options['staff_id'], is_staff=True, is_active=True).first()
        if not profile or not user:
            raise CommandError('Existing active candidate and staff IDs are required.')
        try:
            run, preparation, report = run_daily_workflow(profile, user)
        except Exception:
            raise CommandError('Daily workflow could not finish. Check the dashboard and configuration; private error details suppressed.') from None
        self.stdout.write(f"Discovery #{run.pk}: {run.status}; drafts prepared: {preparation['prepared']}; report #{report.pk}: {report.status}. No automatic submissions.")
        if run.status == 'failed' or report.status != 'sent':
            raise CommandError('Discovery or reporting needs attention. Existing results were preserved; check dashboard statuses.')
