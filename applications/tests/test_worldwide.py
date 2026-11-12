from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from applications.models import Application, DailyReport
from applications.services.daily import prepare_missing_drafts
from applications.services.preparation import job_assessment
from applications.services.reports import report_context, workflow_rows
from applications.tests.test_preparation import make_job, make_profile
from jobs.services.discovery import normalize, source_rows
from jobs.services.discovery_pipeline import classify_record, filter_funnel
from jobs.services.locations import location_group
from jobs.tests.test_discovery import NOW, record
from jobs.tests.test_phase2c import full_row


class WorldwideTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile = make_profile()
        self.uae = make_job(location='Ajman, United Arab Emirates')
        self.other = make_job(source_id='world', url='https://careers.fixture-employer.com/jobs/world', location='London, UK')
        self.staff = get_user_model().objects.create_user(username='worldwide-staff', is_staff=True)
        self.client.force_login(self.staff)

    def test_worldwide_jobs_qualify_across_matching_services(self):
        for location in ('London, UK', 'Riyadh', 'Remote - Europe', 'Remote', 'Ajman', 'United Arab Emirates'):
            with self.subTest(location=location):
                self.assertIsNone(normalize(record(location=location), 'api', 'Python Django', NOW)[1])
                self.assertEqual(classify_record(full_row(location=location), 'api', 'Python Django', NOW,
                    method='authorized_json')[1], 'qualified')
                funnel = filter_funnel(full_row(location=location), 'Python Django', NOW)
                self.assertEqual(funnel['stage.location_pass'], 1)
                self.assertEqual(funnel['stage.cv_pass'], 1)
        self.assertEqual(job_assessment(self.other, self.profile)['status'], 'eligible')
        self.assertEqual(location_group('Dubai'), 'uae')
        self.assertEqual(location_group('Fujairah'), 'uae')
        self.assertEqual(location_group(''), 'unknown')

    def test_dashboard_groups_uae_first_and_preserves_location_filter_on_pagination(self):
        response = self.client.get('/')
        self.assertEqual([r['job'].pk for r in response.context['page']], [self.uae.pk, self.other.pk])
        self.assertContains(response, 'UAE jobs — first priority')
        self.assertContains(response, 'Other locations')
        self.assertEqual(response.context['location_sections'][0][1][0]['job'], self.uae)
        self.assertEqual(response.context['location_sections'][1][1][0]['job'], self.other)
        for i in range(21):
            make_job(source_id=f'world-{i}', url=f'https://careers.fixture-employer.com/jobs/world-{i}', location='Paris, France')
        response = self.client.get('/', {'region': 'other'})
        self.assertEqual(response.context['page'].paginator.count, 22)
        self.assertTrue(all(r['location_group'] == 'other' for r in response.context['page']))
        self.assertContains(response, '?region=other&amp;q=')

    def test_drafts_cover_all_locations_in_priority_order_and_keep_authorization_truthful(self):
        counts = prepare_missing_drafts(self.profile)
        self.assertEqual(counts['prepared'], 2)
        self.assertEqual(list(Application.objects.order_by('pk').values_list('job_id', flat=True)), [self.uae.pk, self.other.pk])
        app = Application.objects.get(job=self.other)
        self.assertTrue(app.draft_answers['work_authorization']['requires_review'])
        self.assertEqual(app.draft_answers['work_authorization']['answer'], '')
        self.assertIsNone(app.submitted_at)

    def test_report_groups_both_locations_and_prioritizes_uae(self):
        rows = workflow_rows(self.profile)
        self.assertEqual([r['job'].pk for r in rows], [self.uae.pk, self.other.pk])
        report = DailyReport(candidate=self.profile, report_date=NOW.date())
        sections = report_context(report)['location_sections']
        self.assertEqual(sections[0][1][0]['job'], self.uae)
        self.assertEqual(sections[1][1][0]['job'], self.other)

    def test_greenhouse_fetches_publication_detail_for_international_roles(self):
        job = {'id': 1, 'title': 'Python Developer', 'location': {'name': 'London'},
               'absolute_url': 'https://example.com/jobs/1', 'content': 'Python Django'}
        responses = iter([{'jobs': [job]}, dict(job, first_published=NOW.isoformat())])
        rows = list(source_rows({'adapter': 'greenhouse', 'board': 'test', 'company': 'Test'}, lambda _: next(responses)))
        self.assertEqual(rows[0]['posted_at'], NOW.isoformat())
