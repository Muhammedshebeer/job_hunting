"""One daily discovery, insert-only preparation, and a deduplicated daily report."""
from django.core.exceptions import ValidationError
from django.utils import timezone
from applications.models import Application
from applications.services.budget import uae_day
from applications.services.preparation import job_assessment, prepare_draft, cv_file_digest
from applications.services.reports import queue_report, process_report_queue
from applications.services.supervised import revalidate_approvals
from jobs.models import DiscoveryRun, Job
from jobs.services.discovery_runs import enqueue_discovery, process_next_run
from jobs.services.discovery_lock import discovery_execution_lock, DiscoveryBusy


def prepare_missing_drafts(profile):
    counts = {'prepared': 0, 'preserved': 0, 'blocked': 0}
    if not cv_file_digest(profile):
        counts['blocked'] = 1
        return counts
    for job in Job.objects.order_by('pk'):
        existing = Application.objects.filter(candidate=profile, job=job).first()
        if existing:
            counts['preserved'] += 1
            continue
        if job_assessment(job, profile)['status'] != 'eligible':
            continue
        try:
            app = prepare_draft(job.pk, profile.pk, only_if_missing=True)
        except ValidationError:
            counts['blocked'] += 1
            continue
        counts['prepared' if app.job_id == job.pk and getattr(app, '_newly_prepared', False) else 'preserved'] += 1
    return counts


def run_daily_job_processing(profile, user):
    if not user.is_active or not user.is_staff or not profile.active or not profile.resume_text.strip():
        raise ValidationError('An active staff identity and candidate with existing CV text are required.')
    with discovery_execution_lock():
        day = uae_day()
        active = DiscoveryRun.objects.filter(status__in=('queued', 'running')).first()
        if active and active.candidate_id != profile.pk:
            raise DiscoveryBusy('Another profile has a queued discovery. Let its worker finish first.')
        # Holding the session lock proves an existing running row is abandoned.
        if active and active.status == 'running':
            DiscoveryRun.objects.filter(pk=active.pk).update(status='failed', finished_at=timezone.now(),
                error_summary='Previous worker stopped. Partial discovery results were retained.')
        run = DiscoveryRun.objects.filter(candidate=profile, scheduled_for=day).first()
        if run is None:
            run, _ = enqueue_discovery(profile, user)
            run.scheduled_for = day
            run.save(update_fields=['scheduled_for'])
        if run.status == 'queued':
            process_next_run()
            run.refresh_from_db()
        revalidate_approvals(dry_run=False, profile_id=profile.pk)
        preparation = prepare_missing_drafts(profile)
        # Discovery/draft approval never authorizes automatic submission.
        return run, preparation


def run_daily_workflow(profile, user):
    """Legacy combined entry point; use the split commands for 07:30 / 08:00."""
    with discovery_execution_lock():
        run, preparation = run_daily_job_processing(profile, user)
        report, _ = queue_report(profile, discovery_run=run)
        process_report_queue(report.pk)
        report.refresh_from_db()
        # A scheduled run never invokes any submission adapter or grants approval.
        return run, preparation, report
