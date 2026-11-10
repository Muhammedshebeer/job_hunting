from django.core.management.base import BaseCommand, CommandError

from jobs.services.discovery_adapters import run_plan
from profiles.models import CandidateProfile


class Command(BaseCommand):
    help = 'Insert-only authorized CSV import with CV qualification and review handling'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--authorization-reference', required=True)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not options['authorization_reference'].strip():
            raise CommandError('An authorization reference is required')
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        if profile is None or not profile.resume_text.strip():
            raise CommandError('Active profile with existing resume_text is required')
        source = {'name': 'manual_csv', 'adapter': 'csv', 'path': options['csv_path'],
                  'enabled': True, 'authorized': True, 'authorization_reference': options['authorization_reference']}
        counts, _, errors = run_plan([source], profile.resume_text, dry_run=options['dry_run'])
        self.stdout.write('Dry run (no database writes)' if options['dry_run'] else 'Authorized CSV import complete')
        for key, value in sorted(counts.items()):
            self.stdout.write(f'{key}: {value}')
        if errors:
            raise CommandError('CSV source failed; private details suppressed')
