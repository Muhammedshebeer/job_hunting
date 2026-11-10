import json
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.services.discovery import DiscoveryError
from jobs.services.discovery_adapters import load_plan, run_plan
from profiles.models import CandidateProfile
from jobs.services.discovery_lock import DiscoveryBusy


class Command(BaseCommand):
    help = 'Run all configured authorized discovery adapters; creates Job records only'

    def add_arguments(self, parser):
        parser.add_argument('--sources', default='discovery_sources.phase2c.json')
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--summary', action='store_true', help='Show platform/adapter groups and totals; omit individual company rows')
        parser.add_argument('--diagnose-dates', action='store_true', help='Whitelisted candidate/date evidence; requires --dry-run')

    def handle(self, *args, **options):
        if options['diagnose_dates'] and not options['dry_run']:
            raise CommandError('--diagnose-dates requires --dry-run')
        try:
            sources = load_plan(options['sources'])
        except DiscoveryError as exc:
            raise CommandError(str(exc)) from None
        if not any(source['enabled'] for source in sources):
            raise CommandError('No enabled authorized sources')
        profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
        if profile is None or not profile.resume_text.strip():
            raise CommandError('Active profile with existing resume_text is required')
        dates = [] if options['diagnose_dates'] else None
        kwargs = {'dry_run': options['dry_run']}
        if dates is not None:
            from jobs.services.date_diagnostics import date_window
            now = timezone.now()
            kwargs.update(now=now, date_diagnostics=dates)
            self.stdout.write('DATE_WINDOW ' + json.dumps(date_window(now)))
        try:
            totals, reports, errors = run_plan(sources, profile.resume_text, **kwargs)
        except DiscoveryBusy:
            raise CommandError('Another discovery process is already running. Wait for it to finish.') from None
        if dates is not None:
            for item in dates:
                self.stdout.write('DATE_CANDIDATE ' + json.dumps(item, ensure_ascii=True))
        self.stdout.write('Dry run (no database writes)' if options['dry_run'] else 'Discovery complete')
        for name, report in reports.items():
            grouped = name.startswith(('adapter.', 'platform.'))
            if options['summary'] and not grouped:
                continue
            prefix = name if grouped else 'source.' + name
            for key, count in sorted(report.items()):
                self.stdout.write(f'{prefix}.{key}: {count}')
        for key, count in sorted(totals.items()):
            self.stdout.write(f'total.{key}: {count}')
        self.stdout.write('No applications created or submitted.')
        if errors:
            raise CommandError('Source failures: ' + ', '.join(errors) + '; details suppressed; other sources processed')
