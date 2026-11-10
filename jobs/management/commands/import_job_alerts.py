from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from jobs.services.alert_mailbox import MailboxError, iter_alert_messages, load_mailbox_config
from jobs.services.job_alerts import import_alert_messages
from profiles.models import CandidateProfile


class Command(BaseCommand):
    help = 'Import authorized platform job-alert emails using read-only TLS IMAP; never submits'

    def add_arguments(self, parser):
        parser.add_argument('--config', default='email_alerts.local.json')
        parser.add_argument('--profile-id', required=True, type=int)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--diagnose-links', action='store_true',
                            help='Opt-in redacted URL-shape counts; requires --dry-run')

    def handle(self, *args, **options):
        counts = Counter()
        if options['diagnose_links'] and not options['dry_run']:
            raise CommandError('--diagnose-links requires --dry-run')
        url_diagnostics = Counter() if options['diagnose_links'] else None
        try:
            config = load_mailbox_config(options['config'])
            profile = CandidateProfile.objects.filter(pk=options['profile_id'], active=True).first()
            if profile is None or not profile.resume_text.strip():
                raise CommandError('Active candidate with existing resume_text is required')
            messages = iter_alert_messages(config, counts)
            try:
                kwargs = {'dry_run': options['dry_run']}
                if url_diagnostics is not None:
                    kwargs['url_diagnostics'] = url_diagnostics
                counts.update(import_alert_messages(messages, profile.resume_text, **kwargs))
            finally:
                messages.close()
        except MailboxError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write('Dry run (no database writes)' if options['dry_run'] else 'Job-alert import complete')
        for key, value in sorted(counts.items()):
            self.stdout.write(f'{key}: {value}')
        if url_diagnostics is not None:
            for (source, host, path, category, reason), count in sorted(url_diagnostics.items()):
                self.stdout.write(f'URL_SHAPE source={source} host={host} path={path} category={category} reason={reason} count={count}')
        self.stdout.write('No applications created or submitted.')
