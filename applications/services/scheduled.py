"""Split scheduled tasks. Retry lock contention only, never an uncertain send."""
import time
from django.conf import settings
from django.core.exceptions import ValidationError
from pathlib import Path
from applications.models import DailyReport
from applications.services.budget import uae_day
from applications.services.reports import queue_report, process_report_queue, report_context, latest_discovery_snapshot
from jobs.services.discovery_adapters import load_plan, run_plan
from jobs.services.discovery_lock import discovery_execution_lock, DiscoveryBusy


def preview_job_agent(profile):
    manifest = getattr(settings, 'DISCOVERY_SOURCES_FILE',
        Path(__file__).resolve().parents[2] / 'discovery_sources.phase2c.json')
    sources = load_plan(manifest)
    if not any(source['enabled'] for source in sources):
        raise ValidationError('No enabled authorized discovery sources are configured.')
    return run_plan(sources, profile.resume_text, dry_run=True)


def preview_job_report(profile, *, test_email=False):
    # Unsaved object: render/assess only; no outbox reservation, credential lookup or SMTP.
    report = DailyReport(candidate=profile, report_date=uae_day(), kind='test' if test_email else 'daily')
    context = report_context(report)
    return {title: len(rows) for title, rows in context['sections']}, context['discovery'], context['discovery_note']


def send_scheduled_report(profile, *, test_email=False, retries=6, retry_seconds=30,
                          retry_failed=False, on_busy=None):
    if not 0 <= retries <= 10 or not 1 <= retry_seconds <= 60:
        raise ValueError('Retry bounds invalid')
    for attempt in range(retries + 1):
        try:
            with discovery_execution_lock():
                report, _ = queue_report(profile, kind='test' if test_email else 'daily', retry_failed=retry_failed,
                    discovery_run=latest_discovery_snapshot(profile))
                if report.candidate_id != profile.pk:
                    raise ValidationError('Today\'s report already belongs to a different configured profile.')
                if report.status == 'queued' and report.discovery_run_id is None:
                    report.discovery_run = latest_discovery_snapshot(report.candidate)
                    report.save(update_fields=['discovery_run'])
                process_report_queue(report.pk)
                report.refresh_from_db()
                return report
        except DiscoveryBusy:
            if attempt == retries:
                raise
            if on_busy:
                on_busy(attempt + 1, retries)
            time.sleep(retry_seconds)
