from django.core.management.base import BaseCommand
from applications.services.supervised import revalidate_approvals


class Command(BaseCommand):
    help = 'Recheck existing approvals without promoting jobs or altering submission evidence'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        checked, invalidated = revalidate_approvals(dry_run=options['dry_run'])
        self.stdout.write(('Dry run (no writes). ' if options['dry_run'] else '') +
                          f'Approvals checked: {checked}; stale approvals: {invalidated}. No submissions made.')
