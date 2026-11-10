from django.core.management.base import BaseCommand, CommandError
from profiles.models import CandidateProfile
from applications.services.reports import queue_report, process_report_queue


class Command(BaseCommand):
    help = 'Send at most one daily report to the authorized personal recipient'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, required=True)
        parser.add_argument('--retry-failed', action='store_true', help='Retry only a known pre-send preparation failure, never an uncertain SMTP outcome')

    def handle(self, *args, **options):
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        if profile is None:
            raise CommandError('An existing active candidate is required.')
        try:
            report, _ = queue_report(profile, retry_failed=options['retry_failed'])
            process_report_queue(report.pk)
            report.refresh_from_db()
        except Exception:
            raise CommandError('Report could not be processed; private details suppressed. Check the recorded status before retrying.') from None
        self.stdout.write(f'Report #{report.pk}: {report.status}. No applications submitted.')
        if report.status != 'sent':
            raise CommandError('Email delivery needs attention. Check dashboard and DAILY_AUTOMATION.md; do not blindly retry uncertain delivery.')
