import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from applications.email_backend import load_delivery_config, DeliveryConfigurationError, CredentialSMTPBackend, REPORT_RECIPIENT
from applications.models import Application, ApplicationEvent, DailyReport, ApplicationSlot
from applications.services.budget import reserve_slot, uae_day
from applications.services.daily import run_daily_workflow
from applications.services.preparation import prepare_draft, review_draft, draft_digest
from applications.services.reports import queue_report, process_report_queue, report_context
from applications.services.routes import application_category, authorized_adapter_for
from applications.services.supervised import open_application, record_manual_submission
from applications.tests.test_preparation import make_job, make_profile, RESOLUTION
from jobs.models import DiscoveryRun
from jobs.services.discovery_adapters import run_plan
from jobs.services.discovery_lock import discovery_execution_lock, DiscoveryBusy
from jobs.tests.test_discovery import NOW
from jobs.tests.test_phase2c import api_source, full_row

CONFIG = {'authorized': True, 'username': REPORT_RECIPIENT, 'from_email': REPORT_RECIPIENT,
          'recipient': REPORT_RECIPIENT, 'host': 'smtp.gmail.com', 'port': 587,
          'use_tls': True, 'use_ssl': False, 'credential_service': 'test-smtp'}


@override_settings(REPORT_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DailyAutomationTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.config = patch('applications.services.reports.load_delivery_config', return_value=CONFIG)
        self.config.start()
        self.addCleanup(self.config.stop)
        self.profile = make_profile()
        self.staff = get_user_model().objects.create_user(username='daily-staff', is_staff=True)
        mail.outbox = []

    def approved(self, job=None):
        job = job or make_job()
        app = prepare_draft(job.pk, self.profile.pk, user=self.staff)
        return review_draft(app.pk, self.staff, resolutions=RESOLUTION, approve=True,
            confirmed=True, expected_digest=draft_digest(app))

    def test_zero_job_html_report_sent_once_even_when_requeued(self):
        report, created = queue_report(self.profile)
        self.assertTrue(created)
        process_report_queue(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.status, 'sent')
        self.assertIsNotNone(report.sent_at)
        duplicate, created = queue_report(self.profile)
        self.assertEqual(duplicate.pk, report.pk)
        self.assertFalse(created)
        self.assertIsNone(process_report_queue(report.pk))
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [REPORT_RECIPIENT])
        self.assertIn('Zero vacancies currently meet all job qualification criteria', email.body)
        self.assertEqual(email.alternatives[0].mimetype, 'text/html')
        for heading in ('Successfully submitted applications', 'Approved applications awaiting submission',
                        'Manual applications required', 'Applications needing clarification', 'Failed submissions', 'Discovery summary'):
            self.assertIn(heading, email.body)
        self.assertFalse(Application.objects.exists())

    def test_daily_report_database_constraint_and_tests_have_separate_allowance(self):
        report, _ = queue_report(self.profile)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailyReport.objects.create(candidate=self.profile, recipient=REPORT_RECIPIENT, report_date=uae_day())
        test, _ = queue_report(self.profile, kind='test')
        process_report_queue(test.pk)
        process_report_queue(report.pk)
        self.assertEqual(len(mail.outbox), 2)

    def test_email_summarizes_confirmed_company_counts_without_claiming_auto_apply(self):
        for index, (company, snapshot, status, reference) in enumerate((
            ('Current employer name', 'Acme & Sons', 'submitted', 'REF-1'),
            ('acme & sons', '', 'submitted', 'REF-2'),
            ('Beta', '', 'submitted', 'REF-3'),
            ('Not submitted', '', 'externally_opened', 'OPENED'),
            ('No evidence', '', 'submitted', ''),
        )):
            job = make_job(source_id=f'summary-{index}',
                url=f'https://careers.fixture-employer.com/jobs/summary-{index}', company=company)
            Application.objects.create(job=job, candidate=self.profile, status=status,
                submitted_at=NOW, confirmation_reference=reference, company_snapshot=snapshot)
        report, _ = queue_report(self.profile)
        counts = report_context(report)['submission_counts']
        self.assertEqual(counts['confirmed_submissions'], 3)
        self.assertEqual(counts['confirmed_company_count'], 2)
        self.assertEqual(counts['confirmed_companies'], [
            {'name': 'Acme & Sons', 'count': 2}, {'name': 'Beta', 'count': 1}])
        self.assertEqual(counts['auto_applied'], 0)
        self.assertEqual(counts['auto_applied_companies'], [])
        process_report_queue(report.pk)
        message = mail.outbox[0]
        self.assertIn('Acme & Sons: 2 applications', message.body)
        self.assertIn('Beta: 1 application', message.body)
        self.assertIn('Companies with confirmed submissions (2)', message.body)
        self.assertIn('Acme &amp; Sons', message.alternatives[0].content)
        self.assertIn('Automatic submission is not configured', message.body)
        self.assertIn('Companies needing review (2 jobs awaiting review or approval)', message.body)
        self.assertIn('Not submitted: 1 job to review', message.body)
        self.assertIn('No evidence: 1 job to review', message.body)

    def test_review_company_summary_groups_jobs_and_includes_pending_approval(self):
        make_job(source_id='review-1', url='https://careers.fixture-employer.com/jobs/review-1',
            company='Review & Co', requires_review=True)
        make_job(source_id='review-2', url='https://careers.fixture-employer.com/jobs/review-2',
            company='review & co', requires_review=True)
        make_job(source_id='pending', url='https://careers.fixture-employer.com/jobs/pending', company='Pending Employer')
        make_job(source_id='rejected', url='https://careers.fixture-employer.com/jobs/rejected',
            company='Excluded Employer', posted_at=NOW - timedelta(days=10))
        report, _ = queue_report(self.profile)
        context = report_context(report)
        self.assertEqual(context['review_job_count'], 3)
        self.assertEqual(context['review_companies'], [
            {'name': 'Pending Employer', 'count': 1}, {'name': 'Review & Co', 'count': 2}])
        self.assertEqual(context['submission_counts']['confirmed_submissions'], 0)
        process_report_queue(report.pk)
        self.assertIn('Review &amp; Co', mail.outbox[0].alternatives[0].content)

    def test_company_summary_uses_report_date_and_selected_candidate(self):
        from applications.services.budget import day_bounds
        start, end = day_bounds(uae_day())
        other = make_profile(name='Other candidate')
        for index, (moment, profile) in enumerate((
            (start, self.profile), (end - timedelta(microseconds=1), self.profile),
            (start - timedelta(microseconds=1), self.profile), (end, self.profile), (NOW, other),
        )):
            job = make_job(source_id=f'boundary-{index}', company=f'Company {index}',
                url=f'https://careers.fixture-employer.com/jobs/boundary-{index}')
            Application.objects.create(job=job, candidate=profile, status='submitted',
                submitted_at=moment, confirmation_reference=f'REF-{index}')
        report, _ = queue_report(self.profile)
        counts = report_context(report)['submission_counts']
        self.assertEqual(counts['confirmed_submissions'], 2)
        self.assertEqual([company['name'] for company in counts['confirmed_companies']], ['Company 0', 'Company 1'])

    def test_only_proven_submissions_include_confirmation_and_time(self):
        app = self.approved()
        # A reference on an approved/opened record is never displayed as a successful submission.
        Application.objects.filter(pk=app.pk).update(confirmation_reference='UNCONFIRMED')
        report, _ = queue_report(self.profile)
        context = report_context(report)
        self.assertEqual(context['sections'][0][1], [])
        Application.objects.filter(pk=app.pk).update(confirmation_reference='')
        open_application(app.pk, self.staff, draft_digest(app))
        record_manual_submission(app.pk, self.staff, confirmed=True, reference='CONFIRMED-123')
        process_report_queue(report.pk)
        self.assertIn('CONFIRMED-123', mail.outbox[0].body)
        self.assertNotIn('UNCONFIRMED', mail.outbox[0].body)
        self.assertEqual(Application.objects.get().status, 'submitted')

    def test_private_cv_answers_and_drafts_are_never_in_report(self):
        self.profile.resume_text += ' PRIVATE_CV_MARKER'
        self.profile.save()
        app = prepare_draft(make_job().pk, self.profile.pk)
        Application.objects.filter(pk=app.pk).update(cover_letter='PRIVATE_LETTER_MARKER')
        report, _ = queue_report(self.profile)
        process_report_queue(report.pk)
        text = mail.outbox[0].body + mail.outbox[0].alternatives[0].content
        self.assertNotIn('PRIVATE_CV_MARKER', text)
        self.assertNotIn('PRIVATE_LETTER_MARKER', text)

    def test_pre_send_configuration_failure_is_safe_to_explicitly_retry(self):
        report, _ = queue_report(self.profile)
        with patch('applications.services.reports.load_delivery_config', side_effect=ValueError('PRIVATE secret')):
            process_report_queue(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.status, 'failed')
        self.assertNotIn('PRIVATE', report.error_summary)
        self.assertEqual(len(mail.outbox), 0)
        queue_report(self.profile)  # No implicit retry.
        self.assertIsNone(process_report_queue(report.pk))
        queue_report(self.profile, retry_failed=True)
        process_report_queue(report.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_uncertain_smtp_exception_is_not_retried_or_exposed(self):
        report, _ = queue_report(self.profile)
        with patch('django.core.mail.message.EmailMessage.send', side_effect=OSError('PRIVATE token')):
            process_report_queue(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.status, 'delivery_unknown')
        self.assertNotIn('PRIVATE', report.error_summary)
        self.assertTrue(report.message_id)
        queue_report(self.profile, retry_failed=True)
        self.assertIsNone(process_report_queue(report.pk))
        self.assertFalse(mail.outbox)

    def test_abandoned_smtp_state_is_not_blindly_retried(self):
        report, _ = queue_report(self.profile)
        DailyReport.objects.filter(pk=report.pk).update(status='sending', message_id='<synthetic@local>')
        self.assertIsNone(process_report_queue())
        report.refresh_from_db()
        self.assertEqual(report.status, 'delivery_unknown')
        self.assertFalse(mail.outbox)

    def test_daily_coordinator_discovers_and_prepares_once_never_approves_or_submits(self):
        def pipeline(sources, cv, **kwargs):
            return run_plan(sources, cv, now=NOW,
                fetcher=lambda _: {'jobs': [full_row(url='https://careers.fixture-employer.com/jobs/live-shaped')]}, **kwargs)
        with patch('jobs.services.discovery_runs.load_plan', return_value=[api_source()]), patch(
                'jobs.services.discovery_runs.run_plan', side_effect=pipeline) as discovery, patch(
                'applications.services.submission.AuthorizedAdapter.submit') as submit:
            run, counts, report = run_daily_workflow(self.profile, self.staff)
            again, repeated, same_report = run_daily_workflow(self.profile, self.staff)
        self.assertEqual(run.pk, again.pk)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(run.scheduled_for, uae_day())
        self.assertEqual(counts['prepared'], 1)
        self.assertEqual(repeated['prepared'], 0)
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(Application.objects.get().status, 'review_required')
        self.assertIsNone(Application.objects.get().approved_at)
        self.assertEqual(same_report.pk, report.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(discovery.call_count, 1)
        submit.assert_not_called()

    def test_scheduled_workflow_preserves_existing_human_review(self):
        app = self.approved()
        original = app.approval_digest
        DiscoveryRun.objects.create(candidate=self.profile, status='completed', scheduled_for=uae_day())
        run_daily_workflow(self.profile, self.staff)
        app.refresh_from_db()
        self.assertEqual(app.approval_digest, original)
        self.assertEqual(app.status, 'approved')
        self.assertIsNone(app.submitted_at)

    def test_other_profile_queued_run_blocks_daily_coordinator(self):
        DiscoveryRun.objects.create(candidate=make_profile())
        with self.assertRaises(DiscoveryBusy):
            run_daily_workflow(self.profile, self.staff)
        self.assertFalse(DailyReport.objects.exists())

    def test_queue_button_only_queues_and_requires_staff_post(self):
        url = reverse('queue_daily_report')
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(DailyReport.objects.exists())
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'profile': self.profile.pk})
        self.client.post(url, {'profile': self.profile.pk})
        self.assertEqual(DailyReport.objects.count(), 1)
        self.assertFalse(mail.outbox)
        response = self.client.get('/')
        for label in ('Auto-Apply Eligible', 'Manual Apply', 'Submitted', 'Needs Review', 'Send Daily Report'):
            self.assertContains(response, label)

    def test_command_test_mail_does_not_send_applications_or_consume_daily_report(self):
        call_command('send_test_email', profile_id=self.profile.pk, stdout=StringIO())
        self.assertEqual(DailyReport.objects.get().kind, 'test')
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(Application.objects.exists())

    def test_no_authorized_auto_adapter_and_audit_events_record_manual_steps(self):
        app = self.approved()
        self.assertIsNone(authorized_adapter_for(app.job))
        self.assertEqual(application_category(app.job, self.profile, app), 'manual_apply')
        open_application(app.pk, self.staff, draft_digest(app))
        record_manual_submission(app.pk, self.staff, confirmed=True, reference='MANUAL-CONF')
        self.assertEqual(set(app.audit_events.values_list('action', flat=True)), {
            'draft_prepared', 'approved_for_manual_opening', 'external_handoff_requested', 'manual_submission_confirmed'})

    def test_daily_twenty_slots_are_global_idempotent_and_preserve_old_attempts(self):
        apps = []
        for i in range(21):
            job = make_job(source_id=str(i), url=f'https://careers.fixture-employer.com/jobs/{i}')
            apps.append(Application.objects.create(job=job, candidate=self.profile))
        for app in apps[:20]:
            reserve_slot(app)
        reserve_slot(apps[0])
        self.assertEqual(ApplicationSlot.objects.count(), 20)
        with self.assertRaises(ValidationError):
            reserve_slot(apps[20])
        with patch('django.utils.timezone.now', return_value=NOW + timedelta(days=1)):
            reserve_slot(apps[20])
        self.assertEqual(ApplicationSlot.objects.count(), 21)

    def test_legacy_attempt_reservations_count_toward_same_limit(self):
        for i in range(20):
            Application.objects.create(candidate=self.profile,
                job=make_job(source_id=str(i), url=f'https://careers.fixture-employer.com/jobs/legacy-{i}'), attempted_at=NOW)
        app = Application.objects.create(candidate=self.profile, job=make_job())
        with self.assertRaises(ValidationError):
            reserve_slot(app)

    def test_limit_blocks_external_handoff_without_recording_open_or_revoking_approval(self):
        app = self.approved()
        for i in range(20):
            other = Application.objects.create(candidate=self.profile,
                job=make_job(source_id=f'cap-{i}', url=f'https://careers.fixture-employer.com/jobs/cap-{i}'))
            reserve_slot(other)
        with self.assertRaises(ValidationError):
            open_application(app.pk, self.staff, draft_digest(app))
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')
        self.assertIsNone(app.externally_opened_at)
        self.assertIsNone(app.submitted_at)

    def test_budget_rollover_is_uae_midnight_not_utc_midnight(self):
        app = Application.objects.create(candidate=self.profile, job=make_job())
        with patch('django.utils.timezone.now', return_value=NOW.replace(hour=19, minute=59)):
            reserve_slot(app)
            first_day = uae_day()
        with patch('django.utils.timezone.now', return_value=NOW.replace(hour=20, minute=0)):
            reserve_slot(app)
            self.assertEqual(uae_day(), first_day + timedelta(days=1))
        self.assertEqual(ApplicationSlot.objects.count(), 2)

    def test_background_preparation_cannot_regenerate_a_concurrently_reviewed_draft(self):
        app = self.approved()
        before = app.approval_digest
        preserved = prepare_draft(app.job_id, app.candidate_id, only_if_missing=True)
        preserved.refresh_from_db()
        self.assertEqual(preserved.status, 'approved')
        self.assertEqual(preserved.approval_digest, before)
        self.assertEqual(app.audit_events.filter(action='draft_regenerated').count(), 0)

    def test_zero_smtp_acceptance_is_unknown_and_never_success(self):
        report, _ = queue_report(self.profile)
        with patch('django.core.mail.message.EmailMessage.send', return_value=0):
            process_report_queue(report.pk)
        report.refresh_from_db()
        self.assertEqual(report.status, 'delivery_unknown')
        self.assertIsNone(report.sent_at)

    def test_missing_date_is_review_and_no_email_receive_date_is_invented(self):
        job = make_job(posted_at=None, requires_review=True, review_reasons=['publication_date_unverified'])
        report, _ = queue_report(self.profile)
        process_report_queue(report.pk)
        self.assertIn('Unverified', mail.outbox[0].body)
        self.assertEqual(application_category(job, self.profile), 'needs_review')
        self.assertIsNone(job.posted_at)

    @override_settings(MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware'])
    def test_report_button_requires_csrf_and_nonstaff_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(reverse('queue_daily_report'), {'profile': self.profile.pk}).status_code, 403)
        ordinary = get_user_model().objects.create_user(username='ordinary-mail')
        self.client.force_login(ordinary)
        self.assertEqual(self.client.post(reverse('queue_daily_report')).status_code, 403)
        self.assertFalse(DailyReport.objects.exists())


class DeliveryConfigurationTests(SimpleTestCase):
    def test_delivery_requires_authorization_tls_exact_recipient_and_no_inline_secret(self):
        invalids = [dict(CONFIG, authorized=False), dict(CONFIG, use_tls=False),
                    dict(CONFIG, port=25), dict(CONFIG, recipient='unexpected@example.com'), dict(CONFIG, password='dummy')]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'smtp.json'
            with override_settings(REPORT_EMAIL_CONFIG=path):
                for config in invalids:
                    path.write_text(json.dumps(config), encoding='utf-8')
                    with self.assertRaises(DeliveryConfigurationError):
                        load_delivery_config()
                path.write_text(json.dumps(CONFIG), encoding='utf-8')
                self.assertEqual(load_delivery_config()['host'], 'smtp.gmail.com')

    def test_smtp_backend_requires_os_keyring_and_never_falls_back_to_plaintext(self):
        with patch('applications.email_backend.load_delivery_config', return_value=CONFIG), patch(
                'applications.email_backend.secure_keyring', side_effect=RuntimeError('PRIVATE')):
            with self.assertRaises(DeliveryConfigurationError) as failure:
                CredentialSMTPBackend()
        self.assertNotIn('PRIVATE', str(failure.exception))
