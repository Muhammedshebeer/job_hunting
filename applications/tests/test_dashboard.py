from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from applications.services.preparation import draft_digest, prepare_draft
from applications.tests.test_preparation import make_job, make_profile
from jobs.tests.test_discovery import NOW


class DashboardTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile, self.job = make_profile(), make_job()
        self.staff = get_user_model().objects.create_user(username='staff', password='test-only-password', is_staff=True)

    def test_dashboard_and_drafts_require_authenticated_staff(self):
        self.assertRedirects(self.client.get('/'), '/accounts/login/?next=/', fetch_redirect_response=False)
        nonstaff = get_user_model().objects.create_user(username='nonstaff')
        self.client.force_login(nonstaff)
        self.assertEqual(self.client.get('/').status_code, 403)
        app = prepare_draft(self.job.pk, self.profile.pk)
        self.assertEqual(self.client.get(reverse('application_detail', args=[app.pk])).status_code, 403)

    def test_dashboard_renders_current_candidate_score_without_writes(self):
        self.client.force_login(self.staff)
        response = self.client.get('/')
        self.assertContains(response, 'Discovered vacancies')
        self.assertEqual(response.context['page'][0]['assessment']['score'], 100)
        self.assertEqual(response.headers['Cache-Control'], 'no-store, private')
        self.assertFalse(Application.objects.exists())
        self.job.refresh_from_db()
        self.assertEqual(self.job.match_score, 40)

    def test_today_counts_use_dubai_boundaries_evidence_and_selected_profile(self):
        from applications.services.budget import day_bounds, uae_day
        from applications.services.reports import submission_counts
        start, end = day_bounds(uae_day())
        for index, (submitted, reference, status, candidate) in enumerate((
            (start, 'CONFIRMED', 'submitted', self.profile),
            (start - timedelta(microseconds=1), 'YESTERDAY', 'submitted', self.profile),
            (end, 'TOMORROW', 'submitted', self.profile),
            (NOW, '', 'submitted', self.profile),
            (NOW, 'OPENED', 'externally_opened', self.profile),
            (NOW, 'OTHER', 'submitted', make_profile(name='Other')),
        )):
            job = make_job(source_id=f'count-{index}', url=f'https://example.com/count-{index}')
            Application.objects.create(job=job, candidate=candidate, status=status,
                submitted_at=submitted, confirmation_reference=reference)
        self.assertEqual(submission_counts(self.profile)['confirmed_submissions'], 1)
        self.assertEqual(submission_counts(self.profile)['auto_applied'], 0)
        self.client.force_login(self.staff)
        response = self.client.get('/', {'q': 'no matching jobs'})
        self.assertEqual(response.context['submission_counts']['confirmed_submissions'], 1)
        self.assertContains(response, 'Auto-applied today (Asia/Dubai)')

    def test_all_four_qualification_filters_and_existing_application_status(self):
        make_job(source_id='old', url='https://example.com/old', posted_at=NOW - timedelta(days=12))
        make_job(source_id='review', url='https://example.com/review', requires_review=True)
        submitted = make_job(source_id='submitted', url='https://example.com/submitted')
        Application.objects.create(job=submitted, candidate=self.profile, status='submitted', confirmation_reference='REF')
        self.client.force_login(self.staff)
        for status in ('eligible', 'rejected', 'needs_review', 'already_applied'):
            with self.subTest(status=status):
                response = self.client.get('/', {'status': status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['page'].paginator.count, 1)
                self.assertEqual(response.context['page'][0]['status'], status)

    def test_prepare_requires_post_and_creates_only_one_draft_without_attempts(self):
        self.client.force_login(self.staff)
        url = reverse('prepare_application', args=[self.job.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'profile': self.profile.pk}).status_code, 302)
        app = Application.objects.get()
        self.assertIsNone(app.attempted_at)
        self.assertIsNone(app.submitted_at)

    def test_draft_review_page_renders_question_form_and_no_submit_action(self):
        app = prepare_draft(self.job.pk, self.profile.pk)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('application_detail', args=[app.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approve draft only')
        self.assertContains(response, 'Human clarification required')
        self.assertNotContains(response, 'action="/submit')
        self.assertIsNone(Application.objects.get().approved_at)

    def test_review_posts_require_explicit_approval_and_verified_clarification(self):
        app = prepare_draft(self.job.pk, self.profile.pk)
        self.client.force_login(self.staff)
        url = reverse('application_detail', args=[app.pk])
        payload = {'action': 'approve', 'revision': draft_digest(app),
                   'resolution_work_authorization': 'Employer confirmed permit transfer is required.',
                   'verification_work_authorization': 'on'}
        self.assertEqual(self.client.post(url, payload).status_code, 200)
        app.refresh_from_db()
        self.assertNotEqual(app.status, 'approved')
        payload['confirmed'] = 'on'
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')
        self.assertIsNone(app.submitted_at)

    def test_job_description_and_company_are_escaped_and_unsafe_links_not_rendered(self):
        self.job.description = '<script>alert("private")</script>'
        self.job.url = 'javascript:alert(1)'
        self.job.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse('job_detail', args=[self.job.pk]))
        self.assertNotContains(response, '<script>alert')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, 'href="javascript:')

    def test_selected_candidate_changes_cv_score_and_missing_profile_is_handled(self):
        other = make_profile(name='Other', resume_text='No recognized skills')
        self.client.force_login(self.staff)
        response = self.client.get('/', {'profile': other.pk})
        self.assertEqual(response.context['page'][0]['assessment']['score'], 0)
        self.assertEqual(self.client.get('/', {'profile': 'invalid'}).status_code, 404)

    @override_settings(MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware'])
    def test_csrf_protects_state_changing_actions(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(reverse('prepare_application', args=[self.job.pk]), {'profile': self.profile.pk}).status_code, 403)
        self.assertFalse(Application.objects.exists())

    def test_login_page_renders_and_admin_is_preserved(self):
        self.assertContains(self.client.get('/accounts/login/'), 'Welcome back.')
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_missing_evidence_is_404_and_never_publicly_served(self):
        app = prepare_draft(self.job.pk, self.profile.pk)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('application_evidence', args=[app.pk])).status_code, 404)
