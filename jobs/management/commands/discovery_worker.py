import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connection

from jobs.services.discovery_lock import DiscoveryBusy, discovery_execution_lock
from jobs.services.discovery_runs import process_next_run
from applications.services.reports import process_report_queue
from applications.services.daily import prepare_missing_drafts


class Command(BaseCommand):
    help = 'Managed PostgreSQL-backed discovery worker; no application submissions'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process at most one queued run, then exit')
        parser.add_argument('--poll-seconds', type=int, default=3)

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('The managed discovery worker requires PostgreSQL session locking')
        if not 1 <= options['poll_seconds'] <= 60:
            raise CommandError('Poll interval must be 1–60 seconds')
        self.stdout.write('Discovery worker ready. Only authorized discovery sources will run.')
        try:
            while True:
                try:
                    with discovery_execution_lock():
                        run = process_next_run()
                        if run:
                            prepared = prepare_missing_drafts(run.candidate)
                            self.stdout.write(f"Discovery #{run.pk}: {run.status}; drafts prepared: {prepared['prepared']}; no applications submitted.")
                        report = process_report_queue()
                        if report:
                            self.stdout.write(f'Report #{report.pk}: {report.status}.')
                except DiscoveryBusy:
                    if options['once']:
                        raise CommandError('Another discovery process owns the worker lock') from None
                if options['once']:
                    return
                close_old_connections()
                time.sleep(options['poll_seconds'])
        except KeyboardInterrupt:
            self.stdout.write('Discovery worker stopped.')
        except CommandError:
            raise
        except Exception:
            raise CommandError('Discovery worker stopped after a database/ownership failure. Private details suppressed; restart the worker to recover its recorded state.') from None
