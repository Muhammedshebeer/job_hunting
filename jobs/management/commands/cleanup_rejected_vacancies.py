from django.core.management.base import BaseCommand

from jobs.services.retention import cleanup_rejected_vacancies


class Command(BaseCommand):
    help = 'Delete rejected vacancies created more than 5 days ago; preserve discovery runs and jobs'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Count expired rows without deleting')

    def handle(self, *args, **options):
        count = cleanup_rejected_vacancies(dry_run=options['dry_run'])
        action = 'Would delete' if options['dry_run'] else 'Deleted'
        self.stdout.write(f'{action} {count} rejected vacancy records older than 5 days.')
