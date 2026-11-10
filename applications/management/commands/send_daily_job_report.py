from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from profiles.models import CandidateProfile
from applications.services.scheduled import preview_job_report, send_scheduled_report
from jobs.services.discovery_lock import DiscoveryBusy


class Command(BaseCommand):
    help = '08:00 Dubai task: deduplicated daily email, with bounded discovery-lock retries'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true', help='Read-only section counts; never reserve or send an email, even with --test-email')
        parser.add_argument('--test-email', action='store_true', help='Send a real TEST report without consuming today\'s daily allowance')
        parser.add_argument('--lock-retries', type=int, default=6)
        parser.add_argument('--retry-seconds', type=int, default=30)
        parser.add_argument('--retry-failed', action='store_true', help='Explicit retry of a known pre-send failure only; uncertain SMTP delivery is never retried')

    def handle(self, *args, **options):
        if not 0 <= options['lock_retries'] <= 10 or not 1 <= options['retry_seconds'] <= 60:
            raise CommandError('Lock retries must be 0..10 and retry seconds 1..60.')
        try:
            if not options['dry_run'] and connection.vendor != 'postgresql':
                raise CommandError('Scheduled reporting requires PostgreSQL session locks.')
            profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
            if profile is None:
                raise CommandError('An existing active candidate ID is required.')
            if options['dry_run']:
                counts, run, note = preview_job_report(profile, test_email=options['test_email'])
                self.stdout.write('Dry run: no database writes, SMTP credential access, emails or submissions.')
                self.stdout.write(f'Discovery snapshot: {run.pk if run else "none"}. {note}')
                for title, count in counts.items():
                    self.stdout.write(f'{title}: {count}')
                return
            report = send_scheduled_report(profile, test_email=options['test_email'],
                retries=options['lock_retries'], retry_seconds=options['retry_seconds'],
                retry_failed=options['retry_failed'], on_busy=lambda attempt, maximum:
                    self.stdout.write(f'Discovery/task lock busy; retry {attempt}/{maximum}. No email queued or sent by this attempt.'))
            self.stdout.write(f'Report #{report.pk}: {report.status}. No applications submitted.')
            if report.status != 'sent':
                raise CommandError('Report delivery needs attention. Inspect recorded status; do not blindly retry uncertain delivery.')
        except CommandError:
            raise
        except DiscoveryBusy:
            raise CommandError('Discovery/task remained busy after bounded retries. No report was queued or sent by this command. Task Scheduler may retry within its configured bound.') from None
        except Exception:
            raise CommandError('Report task failed. Check the recorded report state before retrying; private details suppressed.') from None
