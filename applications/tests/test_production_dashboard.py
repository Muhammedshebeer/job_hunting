import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from applications.models import Application, DailyReport
from applications.tests.test_preparation import make_job, make_profile
from config.database import database_from_url
from jobs.models import DiscoveryRun
from jobs.services.discovery_lock import discovery_execution_lock
from jobs.tests.test_discovery import NOW


class DatabaseUrlTests(SimpleTestCase):
    def test_production_email_backend_cannot_send(self):
        from django.core.mail import send_mail
        with override_settings(EMAIL_BACKEND='config.email_backend.DisabledEmailBackend'):
            with self.assertRaisesMessage(RuntimeError, 'Email delivery is disabled'):
                send_mail('Test', 'Test', 'sender@example.com', ['recipient@example.com'])

    def test_ssl_and_encoded_credentials(self):
        result = database_from_url('postgresql://reader:p%40ss%2Fword@db.example.com/shared?sslmode=verify-full')
        self.assertEqual(result['PASSWORD'], 'p@ss/word')
        self.assertEqual(result['OPTIONS']['sslmode'], 'verify-full')
        self.assertEqual(database_from_url('postgres://reader:pw@db.example.com/shared')['OPTIONS']['sslmode'], 'require')

    def test_invalid_or_unencrypted_urls_do_not_echo_secrets(self):
        for value in ('sqlite:///db', 'postgres://user:PRIVATE@/db',
                      'postgres://u:PRIVATE@db.example.com/db?sslmode=disable',
                      'postgres://u:PRIVATE@db.example.com/db?unexpected=value'):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as error:
                database_from_url(value)
            self.assertNotIn('PRIVATE', str(error.exception))

    def test_production_requires_environment_and_passes_deployment_checks_without_database(self):
        env = os.environ.copy()
        env.update(DJANGO_SETTINGS_MODULE='config.production',
            DJANGO_SECRET_KEY='isolated-production-check-key-abcdefghijklmnopqrstuvwxyz-0123456789',
            DJANGO_ALLOWED_HOSTS='dashboard.example.com',
            DJANGO_CSRF_TRUSTED_ORIGINS='https://dashboard.example.com',
            DATABASE_URL='postgresql://reader:placeholder@127.0.0.1:1/test?sslmode=require')
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, 'manage.py', 'check', '--deploy', '--fail-level', 'WARNING'],
            cwd=root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env.pop('DJANGO_SECRET_KEY')
        result = subprocess.run([sys.executable, 'manage.py', 'check'], cwd=root, env=env,
            capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY is required', result.stderr)


@override_settings(DASHBOARD_READ_ONLY=True,
    SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies',
    MIDDLEWARE=['config.middleware.ReadOnlyDashboardMiddleware',
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware'])
class ReadOnlyDashboardTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile, self.job = make_profile(), make_job()
        self.staff = get_user_model().objects.create_user(username='hosted-staff', password='test-only-password', is_staff=True)
        self.run = DiscoveryRun.objects.create(candidate=self.profile, status='completed')

    def test_login_and_logout_do_not_write_database(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post('/accounts/login/', {'username': 'hosted-staff', 'password': 'test-only-password'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))
        self.staff.refresh_from_db()
        self.assertIsNone(self.staff.last_login)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.post('/accounts/logout/').status_code, 302)

    def test_dashboard_is_private_and_mutations_are_blocked_without_database_queries(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        app = Application.objects.create(candidate=self.profile, job=self.job)
        self.client.force_login(self.staff)
        paths = ['/discovery/run/', '/reports/send/', f'/jobs/{self.job.pk}/prepare/',
            f'/drafts/{app.pk}/', f'/drafts/{app.pk}/open/', f'/drafts/{app.pk}/record-submission/']
        for method in ('post', 'put', 'patch', 'delete'):
            for path in paths:
                with self.subTest(method=method, path=path), self.assertNumQueries(0):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 403)
        self.assertEqual(DiscoveryRun.objects.count(), 1)
        self.assertFalse(DailyReport.objects.exists())
        app.refresh_from_db()
        self.assertEqual(app.status, 'discovered')

    def test_admin_and_private_documents_are_unavailable_even_to_superuser(self):
        self.staff.is_superuser = True
        self.staff.save()
        self.client.force_login(self.staff)
        for path in ('/admin/', '/admin/login/', '/admin/jobs/job/add/', '/media/private/resumes/test.pdf', '/drafts/1/evidence/'):
            for method in ('get', 'post'):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_health_is_public_and_does_not_use_database(self):
        with self.assertNumQueries(0):
            response = self.client.get('/healthz/')
        self.assertEqual(response.json(), {'status': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_inventory_is_readonly_and_exports_counts_without_personal_contents(self):
        import json
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with CaptureQueriesContext(connection) as queries:
            call_command('database_inventory', stdout=output)
        inventory = json.loads(output.getvalue())
        self.assertEqual(inventory['tables']['jobs_job'], 1)
        self.assertEqual(inventory['tables']['profiles_candidateprofile'], 1)
        self.assertTrue(inventory['migrations'])
        self.assertNotIn(self.profile.resume_text, output.getvalue())
        self.assertNotIn(self.staff.username, output.getvalue())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE', 'CREATE')) for q in queries))

    def test_readonly_pages_keep_counts_and_stored_status_without_cv_files(self):
        from config.test_settings import TEMPLATES
        from copy import deepcopy
        templates = deepcopy(TEMPLATES)
        templates[0]['OPTIONS']['context_processors'].append('config.context_processors.deployment_mode')
        app = Application.objects.create(candidate=self.profile, job=self.job, status='approved')
        self.client.force_login(self.staff)
        with override_settings(TEMPLATES=templates), patch('applications.services.preparation.cv_file_digest', side_effect=AssertionError('No hosted CV access')):
            for path in ('/', f'/jobs/{self.job.pk}/', f'/drafts/{app.pk}/', f'/discovery/runs/{self.run.pk}/'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Read-only hosted dashboard')
                self.assertNotContains(response, 'Send Daily Report</button>')
                self.assertNotContains(response, 'Run Discovery</button>')
                self.assertNotContains(response, 'Approve draft only</button>')
                self.assertNotContains(response, 'Open Application')
            self.assertEqual(self.client.get('/').context['counts']['eligible'], 1)

    def test_local_workflows_and_last_login_remain_enabled(self):
        with override_settings(DASHBOARD_READ_ONLY=False):
            self.client.login(username='hosted-staff', password='test-only-password')
            self.staff.refresh_from_db()
            self.assertIsNotNone(self.staff.last_login)
            with discovery_execution_lock():
                pass
        with self.assertRaises(ValidationError):
            with discovery_execution_lock():
                pass

    def test_recorded_evidence_only_submission_does_not_need_hosted_files(self):
        from applications.services.reports import submission_counts
        Application.objects.create(candidate=self.profile, job=self.job, status='submitted',
            submitted_at=NOW, confirmation_evidence='private/evidence/stays-on-windows.pdf')
        self.assertEqual(submission_counts(self.profile)['confirmed_submissions'], 1)
