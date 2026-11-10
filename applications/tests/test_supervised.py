from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from applications.services.preparation import approval_blockers, draft_digest, job_assessment, prepare_draft, review_draft
from applications.services.supervised import current_approval_issues, open_application, record_manual_submission
from applications.tests.test_preparation import make_job, make_profile, RESOLUTION
from jobs import qualification as policy
from jobs.services.date_diagnostics import assess_date, date_window
from jobs.services.discovery import normalize
from jobs.services.discovery_pipeline import classify_record, filter_funnel
from jobs.services.job_alerts import classify_alert
from jobs.services.eligibility import eligible
from jobs.tests.test_discovery import NOW
from jobs.tests.test_phase2c import full_row


class UpdatedPolicyTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile = make_profile(resume_text='Python')

    def test_exact_50_percent_and_ten_days_qualify_across_services(self):
        row = full_row(description='Python FastAPI. Required: 1-3 years of experience.',
                       posted_at=(NOW - timedelta(days=10)).isoformat())
        self.assertEqual(normalize(row, 'api', 'Python', NOW)[0]['match_score'], 50)
        data, outcome = classify_record(row, 'api', 'Python', NOW, method='authorized_json')
        self.assertEqual(outcome, 'qualified')
        self.assertEqual(data['match_score'], 50)
        alert = dict(row, description_verified=True, source='indeed',
                     date_evidence='email JobPosting.datePosted', valid_through=(NOW + timedelta(days=1)).isoformat())
        self.assertEqual(classify_alert(alert, 'Python', NOW)[1], 'qualified')
        self.assertEqual(filter_funnel(row, 'Python', NOW)['stage.cv_pass'], 1)
        job = make_job(description=row['description'], posted_at=NOW - timedelta(days=10))
        self.assertEqual(job_assessment(job, self.profile)['status'], 'eligible')
        self.assertEqual(job_assessment(job, self.profile)['score'], 50)
        job.match_score = 50
        self.assertTrue(eligible(job)[0])
        job.match_score = 49
        self.assertFalse(eligible(job)[0])
        self.assertEqual(assess_date(row['posted_at'], NOW)[1], 'within_publication_window')
        self.assertEqual(date_window(NOW)['inclusive_window_hours'], 240)

    def test_one_microsecond_too_old_future_naive_and_missing_dates_do_not_qualify(self):
        for posted in (NOW - timedelta(days=10, microseconds=1), NOW + timedelta(microseconds=1),
                       NOW.replace(tzinfo=None), None):
            job = make_job(posted_at=posted if posted is None or posted.tzinfo else None, source_id=str(posted),
                           url='https://careers.fixture-employer.com/jobs/' + str(abs(hash(posted))))
            job.posted_at = posted
            self.assertNotEqual(job_assessment(job, self.profile)['status'], 'eligible')

    def test_below_50_and_unknown_evidence_do_not_qualify(self):
        job = make_job(description='Python FastAPI')
        with patch('applications.services.preparation.score_job', return_value=(49, {})):
            self.assertEqual(job_assessment(job, self.profile)['status'], 'rejected')
        for index, changes in enumerate(({'posted_at': None}, {'min_experience': None, 'max_experience': None},
                        {'description': ''}, {'discovery_metadata': {}},
                        {'discovery_metadata': dict(job.discovery_metadata, synthetic=True)})):
            candidate = make_job(source_id=str(index), url=f'https://careers.fixture-employer.com/jobs/unknown-{index}', **changes)
            self.assertNotEqual(job_assessment(candidate, self.profile)['status'], 'eligible')


class SupervisedWorkflowTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile, self.job = make_profile(), make_job()
        self.user = get_user_model().objects.create_user(username='supervisor', is_staff=True)
        self.client.force_login(self.user)

    def approved(self):
        app = prepare_draft(self.job.pk, self.profile.pk)
        return review_draft(app.pk, self.user, resolutions=RESOLUTION, approve=True,
                            confirmed=True, expected_digest=draft_digest(app))

    def opened(self):
        app = self.approved()
        open_application(app.pk, self.user, draft_digest(app))
        app.refresh_from_db()
        return app

    def test_opening_approved_application_redirects_to_stored_url_without_submission(self):
        app = self.approved()
        self.assertContains(self.client.get('/'), 'Open Application')
        response = self.client.post(reverse('open_application', args=[app.pk]), {'revision': draft_digest(app), 'next': 'https://evil.example/'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.job.url)
        app.refresh_from_db()
        self.assertEqual(app.status, 'externally_opened')
        self.assertEqual(app.externally_opened_by_id, self.user.pk)
        self.assertIsNone(app.submitted_at)
        self.assertIsNone(app.attempted_at)
        self.assertFalse(app.confirmation_reference)
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('open_application', args=[app.pk])).status_code, 405)

    def test_unapproved_and_ambiguous_answers_cannot_open(self):
        app = prepare_draft(self.job.pk, self.profile.pk)
        with self.assertRaises(ValidationError):
            open_application(app.pk, self.user, draft_digest(app))
        with self.assertRaises(ValidationError):
            review_draft(app.pk, self.user, resolutions={}, approve=True, confirmed=True, expected_digest=draft_digest(app))
        self.assertNotContains(self.client.get(reverse('application_detail', args=[app.pk])), 'target="_blank"')

    def test_details_page_has_gated_opening_and_separate_confirmation_form(self):
        app = self.approved()
        url = reverse('application_detail', args=[app.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Application details')
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')
        self.assertContains(response, draft_digest(app))
        self.assertNotContains(response, 'href="' + app.application_url)
        self.assertNotContains(response, 'Record completed submission')
        open_application(app.pk, self.user, draft_digest(app))
        self.assertContains(self.client.get(url), 'Record completed submission')

    def test_expiry_invalidates_approval_and_removes_it_from_ready_queue(self):
        app = self.approved()
        with patch('django.utils.timezone.now', return_value=NOW + timedelta(days=10)):
            self.assertNotContains(self.client.get('/'), 'Open Application')
            with self.assertRaises(ValidationError):
                open_application(app.pk, self.user, draft_digest(app))
        app.refresh_from_db()
        self.assertEqual(app.status, 'review_required')
        self.assertIsNone(app.approved_at)
        self.assertTrue(app.cover_letter)

    def test_job_profile_draft_url_or_policy_changes_block_handoff(self):
        mutations = [lambda: self.job.__class__.objects.filter(pk=self.job.pk).update(description='Changed description'),
                     lambda: self.profile.__class__.objects.filter(pk=self.profile.pk).update(resume_text='Changed CV'),
                     lambda: Application.objects.update(cover_letter='Tampered draft'),
                     lambda: Application.objects.update(application_url='https://different-employer.com/job/1')]
        for mutate in mutations:
            app = self.approved()
            mutate()
            with self.assertRaises(ValidationError):
                open_application(app.pk, self.user, draft_digest(app))
            app.refresh_from_db()
            self.assertEqual(app.status, 'review_required')
            self.job.description = 'Build Python Django PostgreSQL services.'
            self.job.save()
            self.profile.resume_text = 'Python Django PostgreSQL'
            self.profile.save()
        app = self.approved()
        with patch.object(policy, 'MIN_CV_SCORE', 51):
            self.assertTrue(current_approval_issues(app))

    def test_synthetic_unknown_source_and_missing_cv_are_blocked(self):
        for changes in ({'url': 'https://example.com/demo'}, {'discovery_metadata': {}},
                        {'discovery_metadata': dict(self.job.discovery_metadata, synthetic=True)}):
            for name, value in changes.items():
                setattr(self.job, name, value)
            self.job.save()
            app = prepare_draft(self.job.pk, self.profile.pk)
            self.assertTrue(approval_blockers(app))
        self.profile.resume = ''
        self.profile.save()
        self.assertTrue(approval_blockers(app))

    def test_manually_record_reference_only_after_opening_and_explicit_confirmation(self):
        app = self.approved()
        with self.assertRaises(ValidationError):
            record_manual_submission(app.pk, self.user, confirmed=True, reference='CONF-123')
        # A refusal revoked the unusable approval. Review and open afresh.
        app = self.opened()
        for confirmed, reference in ((False, 'CONF-123'), (True, '')):
            with self.assertRaises(ValidationError):
                record_manual_submission(app.pk, self.user, confirmed=confirmed, reference=reference)
        result = record_manual_submission(app.pk, self.user, confirmed=True, reference='CONF-123')
        self.assertEqual(result.status, 'submitted')
        self.assertEqual(result.confirmation_reference, 'CONF-123')
        self.assertEqual(result.submission_recorded_by_id, self.user.pk)
        self.assertEqual(result.submitted_at, NOW)
        self.assertIsNone(result.attempted_at)
        again = record_manual_submission(app.pk, self.user, confirmed=True, reference='REPLACE')
        self.assertEqual(again.confirmation_reference, 'CONF-123')
        with self.assertRaises(ValidationError):
            open_application(app.pk, self.user, draft_digest(app))

    def test_evidence_upload_is_private_bounded_and_cannot_overwrite_confirmation(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.profile = make_profile()
            app = self.opened()
            evidence = SimpleUploadedFile('confirmation.pdf', b'%PDF-1.4 synthetic confirmation', content_type='application/pdf')
            response = self.client.post(reverse('record_submission', args=[app.pk]), {'confirmed': 'on', 'evidence': evidence})
            self.assertEqual(response.status_code, 302)
            app.refresh_from_db()
            self.assertEqual(app.status, 'submitted')
            self.assertTrue(app.confirmation_evidence.name.startswith('private/evidence/'))
            download = self.client.get(reverse('application_evidence', args=[app.pk]))
            self.assertEqual(download.status_code, 200)
            download.close()

    def test_upload_validation_and_attestation_do_not_record_submission(self):
        app = self.opened()
        url = reverse('record_submission', args=[app.pk])
        for data in ({'reference': 'X'}, {'confirmed': 'on'},
                     {'confirmed': 'on', 'evidence': SimpleUploadedFile('bad.pdf', b'<script>')},
                     {'confirmed': 'on', 'evidence': SimpleUploadedFile('bad.html', b'%PDF-1.4')}):
            self.assertEqual(self.client.post(url, data).status_code, 200)
        app.refresh_from_db()
        self.assertEqual(app.status, 'externally_opened')
        self.assertFalse(app.confirmation_evidence)

    def test_terminal_confirmation_survives_policy_revalidation(self):
        app = self.opened()
        record_manual_submission(app.pk, self.user, confirmed=True, reference='ORIGINAL')
        with patch.object(policy, 'PUBLICATION_WINDOW_DAYS', 1):
            call_command('revalidate_approvals', stdout=StringIO())
        app.refresh_from_db()
        self.assertEqual(app.status, 'submitted')
        self.assertEqual(app.confirmation_reference, 'ORIGINAL')
        self.assertEqual(app.submitted_at, NOW)

    def test_oversized_evidence_is_refused_without_writing_a_file(self):
        app = self.opened()
        oversized = SimpleUploadedFile('large.pdf', b'%PDF-' + b'x' * (5 * 1024 * 1024))
        with self.assertRaises(ValidationError):
            record_manual_submission(app.pk, self.user, confirmed=True, evidence=oversized)
        app.refresh_from_db()
        self.assertFalse(app.confirmation_evidence)
        self.assertEqual(app.status, 'externally_opened')

    def test_duplicate_vacancy_already_submitted_cannot_open_again(self):
        app = self.approved()
        duplicate = make_job(source_id='other', url=self.job.url + '?utm_source=other')
        Application.objects.create(candidate=self.profile, job=duplicate, status='submitted', confirmation_reference='EXISTING')
        with self.assertRaises(ValidationError):
            open_application(app.pk, self.user, draft_digest(app))
        self.assertEqual(prepare_draft(duplicate.pk, self.profile.pk).pk, app.pk)

    def test_revalidation_dry_run_is_read_only_and_real_run_preserves_evidence(self):
        app = self.approved()
        Application.objects.filter(pk=app.pk).update(preparation_snapshot={})
        output = StringIO()
        call_command('revalidate_approvals', dry_run=True, stdout=output)
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')
        self.assertIn('stale approvals: 1', output.getvalue())
        call_command('revalidate_approvals', stdout=StringIO())
        app.refresh_from_db()
        self.assertEqual(app.status, 'review_required')
        self.assertTrue(app.cover_letter)
        self.assertEqual(Application.objects.count(), 1)

    def test_actions_require_staff_and_csrf(self):
        app = self.approved()
        ordinary = get_user_model().objects.create_user(username='ordinary-supervised')
        self.client.force_login(ordinary)
        for name in ('open_application', 'record_submission'):
            self.assertEqual(self.client.post(reverse(name, args=[app.pk])).status_code, 403)

    @override_settings(MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware'])
    def test_csrf_required_for_both_supervised_actions(self):
        app = self.approved()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for name in ('open_application', 'record_submission'):
            self.assertEqual(client.post(reverse(name, args=[app.pk]), {'revision': draft_digest(app)}).status_code, 403)
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')

    def test_exact_policy_boundary_can_open_but_expires_one_microsecond_later(self):
        self.profile.resume_text = 'Python'
        self.profile.save()
        self.job.description, self.job.posted_at = 'Python FastAPI. Required: 1-3 years experience.', NOW - timedelta(days=10)
        self.job.save()
        app = self.approved()
        self.assertEqual(open_application(app.pk, self.user, draft_digest(app)), self.job.url)
        with patch('django.utils.timezone.now', return_value=NOW + timedelta(microseconds=1)):
            with self.assertRaises(ValidationError):
                open_application(app.pk, self.user, draft_digest(app))
        app.refresh_from_db()
        self.assertEqual(app.status, 'review_required')
        self.assertIsNotNone(app.externally_opened_at)
        self.assertIsNone(app.submitted_at)

    def test_replacing_cv_bytes_at_same_path_revokes_approval(self):
        app = self.approved()
        with self.profile.resume.storage.open(self.profile.resume.name, 'wb') as uploaded:
            uploaded.write(b'%PDF-1.4 replaced synthetic CV')
        with self.assertRaises(ValidationError):
            open_application(app.pk, self.user, draft_digest(app))
        app.refresh_from_db()
        self.assertEqual(app.status, 'review_required')
