from django.core.management.base import BaseCommand, CommandError
from profiles.models import CandidateProfile
from applications.services.reports import queue_report, process_report_queue


class Command(BaseCommand):
    help = 'Send an explicitly requested test report to the configured personal recipient'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, required=True)

    def handle(self, *args, **options):
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        if profile is None:
            raise CommandError('An existing active candidate is required.')
        try:
            report, _ = queue_report(profile, kind='test')
            process_report_queue(report.pk)
            report.refresh_from_db()
        except Exception:
            raise CommandError('Test email failed; private details suppressed. Inspect the report status before sending another test.') from None
        self.stdout.write(f'Test email #{report.pk}: {report.status}. Daily-report allowance was not consumed.')
        if report.status != 'sent':
            raise CommandError('Test delivery needs attention. See DAILY_AUTOMATION.md.')
