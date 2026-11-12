"""Durable local discovery queue. All remote I/O happens in the worker."""
from jobs import qualification as policy
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from jobs.models import DiscoveryRun, RejectedVacancy
from jobs.services.date_diagnostics import AMBIGUOUS_DATE_REASONS, safe_job_text
from jobs.services.discovery_adapters import load_plan, run_plan
from jobs.services.discovery_lock import discovery_execution_lock
from jobs.services.job_alerts import parse_publication


REJECTION_LABELS = {
    'rejected_role': 'Role is outside the approved developer roles.',
    'rejected_location': 'Location did not meet the location policy used for this historical run.',
    'rejected_date': f'Publication date is outside the verified last-{policy.PUBLICATION_WINDOW_DAYS}-days window.',
    'rejected_experience': 'Experience requirements do not overlap 1–3 years.',
    'rejected_score': f'Verified-description CV relevance is below {policy.MIN_CV_SCORE}%.',
    'rejected_closed': 'The source reports applications closed or the vacancy expired.',
    'invalid_url': 'A valid, safe job URL could not be established.',
    'invalid_fields': 'Source fields are invalid or exceed supported limits.',
    'invalid_record': 'The source did not return a usable vacancy record.',
    'invalid_platform': 'The record does not identify a supported alert platform.',
    'unverified_job_link': 'The link is not a verified job-detail destination.',
}


def rejection_label(code, values=None):
    if values and code == 'rejected_date':
        return f"Publication date is outside the verified last-{values['publication_window_days']}-days window."
    if values and code == 'rejected_score':
        return f"Verified-description CV relevance is below {values['minimum_cv_score']}%."
    return REJECTION_LABELS.get(code, 'Vacancy did not pass the existing qualification checks.')


def run_policy(run):
    # Historical snapshots retain their original policy; they are not requalified.
    return {'minimum_cv_score': run.totals.get('policy.minimum_cv_score', 75),
            'publication_window_days': run.totals.get('policy.publication_window_days', 7),
            'location_scope': run.totals.get('policy.location_scope', 'uae_only')}


def summary_counts(counts):
    return {'fetched': counts.get('fetched', 0), 'roles': counts.get('stage.role_pass', 0),
            'uae': counts.get('stage.uae_pass', 0), 'recent': counts.get('stage.date_pass', 0),
            'eligible': counts.get('eligible', 0), 'review': counts.get('review', 0),
            'rejected': counts.get('rejected', 0), 'failed': counts.get('failed_sources', 0)}


def enqueue_discovery(profile, user):
    if not user.is_active or not user.is_staff:
        raise ValidationError('An active staff account is required.')
    if not profile.active or not profile.resume_text.strip():
        raise ValidationError('An active profile with existing extracted CV text is required.')
    # The partial unique constraint resolves races between different HTTP requests.
    for _ in range(2):
        active = DiscoveryRun.objects.filter(status__in=('queued', 'running')).first()
        if active:
            return active, False
        try:
            with transaction.atomic():
                return DiscoveryRun.objects.create(candidate=profile, requested_by=user), True
        except IntegrityError:
            continue
    raise ValidationError('Another discovery request is being queued. Refresh the history.')


def _execute_run(run, owner):
    """Caller owns the global session lock for the full lifecycle."""
    policy_counts = {'policy.minimum_cv_score': policy.MIN_CV_SCORE,
                     'policy.publication_window_days': policy.PUBLICATION_WINDOW_DAYS,
                     'policy.location_scope': 'worldwide_uae_first'}
    run.totals = dict(run.totals, **policy_counts)
    def touch():
        owner.check()
        DiscoveryRun.objects.filter(pk=run.pk, status='running').update(heartbeat_at=timezone.now())

    def rejected(row, origin, reason):
        owner.check()
        raw = row if isinstance(row, dict) else {}
        ambiguous = raw.get('_publication_date_reason')
        date = None if isinstance(ambiguous, str) and ambiguous in AMBIGUOUS_DATE_REASONS else parse_publication(raw.get('posted_at'))
        # No URL, source payload, email body, CV, or credential is retained.
        RejectedVacancy.objects.create(run=run, source=str(origin)[:50],
            company=safe_job_text(raw.get('company')), title=safe_job_text(raw.get('title')),
            location=safe_job_text(raw.get('location'))[:200], posted_at=date,
            rejection_code=reason if reason in REJECTION_LABELS else 'qualification_failed')

    def source_finished(name, counts, totals):
        owner.check()
        run.source_counts[name] = counts
        run.totals = dict(totals, **policy_counts)
        DiscoveryRun.objects.filter(pk=run.pk, status='running').update(
            source_counts=run.source_counts, totals=run.totals, heartbeat_at=timezone.now())

    try:
        profile = run.candidate
        if not profile.active or not profile.resume_text.strip():
            raise ValidationError('Candidate evidence unavailable')
        manifest = getattr(settings, 'DISCOVERY_SOURCES_FILE',
                           Path(settings.BASE_DIR) / 'discovery_sources.phase2c.json' if hasattr(settings, 'BASE_DIR')
                           else Path(__file__).resolve().parents[2] / 'discovery_sources.phase2c.json')
        sources = load_plan(manifest)
        run.source_names = [source['name'] for source in sources if source['enabled']]
        if not run.source_names:
            raise ValidationError('No enabled authorized source')
        touch()
        DiscoveryRun.objects.filter(pk=run.pk, status='running').update(source_names=run.source_names, totals=run.totals)
        totals, reports, errors = run_plan(sources, profile.resume_text, dry_run=False,
                                          on_rejected=rejected, on_source=source_finished)
        owner.check()
        run.totals, run.source_counts = dict(totals, **policy_counts), {name: dict(counts) for name, counts in reports.items()}
        run.status = 'failed' if errors else 'completed'
        run.error_summary = 'Some sources failed. Successful-source results and audit rows were retained; retry safely.' if errors else ''
    except BaseException as exc:
        # Do not echo remote responses, mailbox/keyring exceptions, or CV data.
        run.status = 'failed'
        run.error_summary = 'Discovery stopped before completion. Check the worker and authorized source configuration. Private error details were suppressed.'
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            owner.check()
            DiscoveryRun.objects.filter(pk=run.pk, status='running').update(
                status='failed', error_summary=run.error_summary, finished_at=timezone.now())
            raise
    owner.check()
    run.finished_at = timezone.now()
    DiscoveryRun.objects.filter(pk=run.pk, status='running').update(
        status=run.status, finished_at=run.finished_at, heartbeat_at=run.finished_at,
        totals=run.totals, source_counts=run.source_counts, error_summary=run.error_summary)
    run.refresh_from_db()
    return run


def process_next_run():
    with discovery_execution_lock() as owner:
        owner.check()
        # Holding the session lock proves no other managed worker is executing.
        # A running row left behind must be from a worker that exited/crashed.
        DiscoveryRun.objects.filter(status='running').update(status='failed', finished_at=timezone.now(),
            error_summary='The previous worker stopped before completion. Partial results were preserved. Queue a new run to retry.')
        with transaction.atomic():
            run = DiscoveryRun.objects.select_for_update().filter(status='queued').order_by('queued_at').first()
            if run is None:
                return None
            run.status, run.started_at, run.heartbeat_at = 'running', timezone.now(), timezone.now()
            run.save(update_fields=['status', 'started_at', 'heartbeat_at'])
        return _execute_run(run, owner)
