from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from applications.models import Application
from applications.tests.test_preparation import make_profile
from jobs.models import DiscoveryRun, Job, RejectedVacancy
from jobs.tests.test_discovery import NOW


class DiscoveryDashboardTests(TestCase):
    def setUp(self):
        self.profile = make_profile()
        self.staff = get_user_model().objects.create_user(username='staff-discovery', is_staff=True)
        self.run = DiscoveryRun.objects.create(candidate=self.profile, status='completed',
            totals={'fetched': 112, 'stage.role_pass': 24, 'stage.uae_pass': 13, 'stage.date_pass': 0,
                    'eligible': 0, 'review': 0, 'rejected': 112},
            source_names=['synthetic_board'], source_counts={'synthetic_board': {'fetched': 112, 'rejected': 112}})
        self.audit = RejectedVacancy.objects.create(run=self.run, company='Synthetic Employer',
            title='Python Developer', location='Dubai', source='synthetic_board',
            posted_at=NOW, rejection_code='rejected_date')
        self.detail = reverse('discovery_run_detail', args=[self.run.pk])
        self.status = reverse('discovery_run_status', args=[self.run.pk])
        self.queue = reverse('queue_discovery')

    def test_all_discovery_routes_require_staff(self):
        for url in (self.detail, self.status, self.queue):
            self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user(username='ordinary')
        self.client.force_login(user)
        for url in (self.detail, self.status, self.queue):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_dashboard_includes_history_when_no_eligible_jobs_exist(self):
        self.client.force_login(self.staff)
        response = self.client.get('/')
        self.assertContains(response, 'Discovery history')
        self.assertContains(response, '112')
        self.assertContains(response, 'Run Discovery')
        self.assertEqual(response.context['discovery_runs'][0].summary['uae'], 13)
        self.assertEqual(response.context['discovery_runs'][0].policy['publication_window_days'], 7)
        self.assertFalse(Job.objects.exists())

    def test_queue_endpoint_only_persists_request_and_duplicate_requests_share_run(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.queue).status_code, 405)
        with patch('jobs.services.discovery_runs.run_plan') as pipeline:
            first = self.client.post(self.queue, {'profile': self.profile.pk})
            second = self.client.post(self.queue, {'profile': self.profile.pk})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, second.url)
        self.assertEqual(DiscoveryRun.objects.filter(status='queued').count(), 1)
        pipeline.assert_not_called()
        self.assertFalse(Application.objects.exists())

    def test_audit_and_status_are_read_only_and_do_not_expose_private_content(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.detail)
        self.assertContains(response, 'Synthetic Employer')
        self.assertContains(response, 'Publication date is outside')
        self.assertNotContains(response, self.profile.resume_text)
        self.assertNotContains(response, '/prepare/')
        self.assertEqual(self.client.post(self.detail).status_code, 405)
        self.assertEqual(self.client.post(self.status).status_code, 405)
        payload = self.client.get(self.status)
        self.assertEqual(payload['Cache-Control'], 'no-store, private')
        self.assertEqual(payload.json()['summary']['fetched'], 112)
        self.assertEqual(payload.json()['sources'][0]['state'], 'completed')
        self.assertFalse(Application.objects.exists())
        self.assertFalse(Job.objects.exists())
        self.assertEqual(RejectedVacancy.objects.count(), 1)

    def test_audit_filters_and_pagination(self):
        RejectedVacancy.objects.bulk_create([RejectedVacancy(run=self.run, source='other',
            rejection_code='rejected_role', title=f'Other {i}') for i in range(26)])
        self.client.force_login(self.staff)
        response = self.client.get(self.detail, {'audit_source': 'other', 'page': 2})
        self.assertEqual(response.context['audit_page'].paginator.count, 26)
        self.assertEqual(len(response.context['audit_page']), 1)
        response = self.client.get(self.detail, {'reason': 'rejected_date'})
        self.assertEqual(response.context['audit_page'].paginator.count, 1)

    def test_queue_rejects_profile_without_resume_evidence(self):
        self.profile.resume_text = ''
        self.profile.save()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(self.queue, {'profile': self.profile.pk}).status_code, 302)
        self.assertFalse(DiscoveryRun.objects.filter(status='queued').exists())

    @override_settings(MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware'])
    def test_run_discovery_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.queue, {'profile': self.profile.pk}).status_code, 403)
        self.assertFalse(DiscoveryRun.objects.filter(status='queued').exists())
