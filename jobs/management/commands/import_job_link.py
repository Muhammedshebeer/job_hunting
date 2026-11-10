from django.core.management.base import BaseCommand, CommandError

from jobs.services.alert_mailbox import PLATFORM_DOMAINS
from jobs.services.discovery_adapters import read_json
from jobs.services.discovery import DiscoveryError
from jobs.services.discovery_pipeline import ingest_rows
from profiles.models import CandidateProfile


class Command(BaseCommand):
    help = 'Record a user-supplied job link/metadata without fetching the page; incomplete jobs need review'

    def add_arguments(self, parser):
        parser.add_argument('--platform', required=True, choices=tuple(PLATFORM_DOMAINS))
        parser.add_argument('--url', required=True)
        parser.add_argument('--metadata', help='Optional local JSON object of manually verified job facts')
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        if profile is None or not profile.resume_text.strip():
            raise CommandError('Active profile with existing resume_text is required')
        try:
            row = read_json(options['metadata']) if options['metadata'] else {}
        except DiscoveryError as exc:
            raise CommandError(str(exc)) from None
        if not isinstance(row, dict):
            raise CommandError('Metadata must be a JSON object')
        row.update(source=options['platform'], url=options['url'])
        counts = ingest_rows([row], options['platform'], profile.resume_text,
                             method='manual_link', platform_rows=True, dry_run=options['dry_run'])
        self.stdout.write('Dry run (no database writes)' if options['dry_run'] else 'Manual job-link import complete')
        for key, value in sorted(counts.items()):
            self.stdout.write(f'{key}: {value}')
        self.stdout.write('No page fetched; no applications created or submitted.')
