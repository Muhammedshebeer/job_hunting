from django.core.management.base import BaseCommand, CommandError

from jobs.services.discovery import DiscoveryError, discover, load_sources
from profiles.models import CandidateProfile


class Command(BaseCommand):
    help = 'Discover recent matching jobs from configured authorized APIs; never submits applications'

    def add_arguments(self, parser):
        parser.add_argument('--sources', required=True, help='Discovery JSON configuration path')
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true', help='Fetch/filter without writing jobs')

    def handle(self, *args, **options):
        try:
            sources = load_sources(options['sources'])
        except DiscoveryError as exc:
            raise CommandError(str(exc)) from None
        if not any(source['enabled'] for source in sources):
            raise CommandError('No enabled authorized discovery sources; configure --sources first')
        try:
            profile = CandidateProfile.objects.get(pk=options['profile_id'], active=True)
        except CandidateProfile.DoesNotExist:
            raise CommandError('Active candidate profile not found') from None
        if not profile.resume_text.strip():
            raise CommandError('Existing extracted resume_text is required; no CV will be uploaded')
        counts, errors = discover(sources, profile.resume_text, dry_run=options['dry_run'])
        self.stdout.write('Dry run (no writes)' if options['dry_run'] else 'Discovery complete')
        for key, count in sorted(counts.items()):
            self.stdout.write(f'{key}: {count}')
        for name, reason in errors:
            self.stderr.write(f'{name}: {reason}')
        if errors:
            raise CommandError('One or more sources failed; successful sources were processed')
