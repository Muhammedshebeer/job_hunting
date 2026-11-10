from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from profiles.models import CandidateProfile
from applications.services.daily import run_daily_job_processing
from applications.services.scheduled import preview_job_agent
from jobs.services.discovery_lock import DiscoveryBusy


class Command(BaseCommand):
    help = '07:30 Dubai task: authorized discovery and supervised drafts; no emails or unattended submissions'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--staff-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true', help='Authorized read-only discovery; no database writes, emails, approvals or submissions')

    def handle(self, *args, **options):
        try:
            if not options['dry_run'] and connection.vendor != 'postgresql':
                raise CommandError('Scheduled execution requires PostgreSQL session locks.')
            profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
            user = get_user_model().objects.filter(pk=options['staff_id'], is_staff=True, is_active=True).first()
            if not profile or not profile.resume_text.strip() or not user:
                raise CommandError('Existing active candidate with CV text and active staff IDs are required.')
            if options['dry_run']:
                totals, _, errors = preview_job_agent(profile)
                self.stdout.write('Dry run: no database writes, emails, approvals or submissions.')
                for key in ('fetched', 'eligible', 'review', 'rejected', 'duplicates', 'would_create', 'failed_sources'):
                    self.stdout.write(f'{key}: {totals.get(key, 0)}')
                if errors:
                    raise CommandError('Some authorized sources failed; private details suppressed.')
                return
            run, preparation = run_daily_job_processing(profile, user)
            self.stdout.write(f"Discovery #{run.pk}: {run.status}; drafts prepared: {preparation['prepared']}; preserved: {preparation['preserved']}; blocked: {preparation['blocked']}. No emails or automatic submissions.")
            if run.status != 'completed' or preparation['blocked']:
                raise CommandError('Discovery or draft preparation needs attention. Saved results were preserved.')
        except CommandError:
            raise
        except DiscoveryBusy:
            raise CommandError('Another discovery/task owns the database lock. Retry after it finishes; no overlapping run was started.') from None
        except Exception:
            raise CommandError('Job-agent task failed. Check recorded run status and configuration; private details suppressed.') from None
