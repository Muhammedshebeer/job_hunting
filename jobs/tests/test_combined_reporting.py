from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from applications.models import Application
from jobs.models import Job
from jobs.services.discovery_adapters import run_plan
from jobs.services.discovery_pipeline import filter_funnel
from jobs.tests.test_discovery import NOW, configuration
from jobs.tests.test_job_alerts import CONFIG, email_message
from jobs.tests.test_phase2c import api_source, full_row
from profiles.models import CandidateProfile


class FunnelTests(SimpleTestCase):
    def test_full_record_passes_every_diagnostic_filter_without_mutating_input(self):
        row = full_row()
        before = dict(row)
        counts = filter_funnel(row, 'python django', NOW)
        for key in ('role_pass', 'uae_pass', 'date_pass', 'experience_pass', 'cv_pass'):
            self.assertEqual(counts['stage.' + key], 1)
        self.assertEqual(row, before)

    def test_role_location_date_experience_and_score_have_cumulative_semantics(self):
        keys = ['role_pass', 'uae_pass', 'date_pass', 'experience_pass', 'cv_pass']
        for row, cv, passes in (
            (full_row(title='Accountant'), 'python django', 0),
            (full_row(location='London'), 'python django', 1),
            (full_row(posted_at=(NOW - timedelta(days=11)).isoformat()), 'python django', 2),
            (full_row(min_experience=5, max_experience=8), 'python django', 3),
            (full_row(), 'unrelated', 4),
        ):
            with self.subTest(passes=passes):
                counts = filter_funnel(row, cv, NOW)
                self.assertEqual([counts['stage.' + key] for key in keys], [int(index < passes) for index in range(5)])

    def test_audit_reports_old_relevant_uae_jobs_without_qualifying_them(self):
        counts = filter_funnel(full_row(posted_at=(NOW - timedelta(days=11)).isoformat()), 'python django', NOW)
        self.assertEqual(counts['audit.uae_role_matches'], 1)
        self.assertEqual(counts['audit.recent_matches'], 0)
        self.assertEqual(counts['stage.date_pass'], 0)

    def test_naive_missing_future_and_exact_ten_day_boundary(self):
        for date, passed, unknown in ((None, 0, 1), ('2026-11-09T12:00:00', 0, 1),
                                     ((NOW + timedelta(seconds=1)).isoformat(), 0, 0),
                                     ((NOW - timedelta(days=10)).isoformat(), 1, 0)):
            counts = filter_funnel(full_row(posted_at=date), 'python django', NOW)
            self.assertEqual(counts['stage.date_pass'], passed)
            self.assertEqual(counts['stage.date_unknown'], unknown)

    def test_unknown_experience_is_not_a_pass(self):
        counts = filter_funnel(full_row(description='Python Django', min_experience=None, max_experience=None), 'python django', NOW)
        self.assertEqual(counts['stage.experience_pass'], 0)
        self.assertEqual(counts['stage.experience_unknown'], 1)


class CombinedReportingTests(TestCase):
    def test_all_requested_groups_are_present_with_zero_platform_counts(self):
        totals, reports, errors = run_plan([api_source()], 'python django', dry_run=True, now=NOW,
                                         fetcher=lambda _: {'jobs': [full_row()]})
        self.assertFalse(errors)
        for name in ('adapter.greenhouse', 'adapter.ashby', 'platform.linkedin', 'platform.indeed',
                     'platform.naukrigulf', 'platform.bayt'):
            self.assertIn(name, reports)
            self.assertEqual(reports[name]['fetched'], 0)
        self.assertEqual(totals['fetched'], 1)  # Group rows never double-count totals.

    def test_email_messages_without_jobs_still_report_the_platform(self):
        with configuration(CONFIG) as mailbox_path:
            source = dict(api_source('alerts'), adapter='email_alerts', path=mailbox_path)
            messages = (row for row in [('naukrigulf', email_message('naukrigulf', html='<p>No direct job links</p>'))])
            with patch('jobs.services.discovery_adapters.iter_alert_messages', return_value=messages):
                totals, reports, errors = run_plan([source], 'python django', dry_run=True, now=NOW)
        self.assertFalse(errors)
        self.assertEqual(reports['platform.naukrigulf']['messages_read'], 1)
        self.assertEqual(reports['platform.naukrigulf']['configured_senders'], 1)
        self.assertEqual(reports['platform.naukrigulf']['fetched'], 0)
        self.assertEqual(reports['platform.linkedin']['messages_read'], 0)
        self.assertEqual(totals['fetched'], 0)

    def test_email_and_feed_group_reports_do_not_double_count_or_write(self):
        with configuration(CONFIG) as mailbox_path:
            source = dict(api_source('alerts'), adapter='email_alerts', path=mailbox_path)
            messages = (row for row in [('indeed', email_message('indeed'))])
            with patch('jobs.services.discovery_adapters.iter_alert_messages', return_value=messages), CaptureQueriesContext(connection) as queries:
                totals, reports, errors = run_plan([api_source(), source], 'PRIVATE python django', dry_run=True, now=NOW,
                                                 fetcher=lambda _: {'jobs': [full_row()]})
        self.assertFalse(errors)
        self.assertEqual(reports['platform.indeed']['eligible'], 1)
        self.assertEqual(totals['fetched'], 2)
        self.assertEqual(totals['stage.role_pass'], 2)
        self.assertEqual(totals['stage.eligible'], 2)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertNotIn('PRIVATE', repr(reports))
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_stage_cv_pass_does_not_make_missing_evidence_eligible(self):
        totals, _, errors = run_plan([api_source()], 'python django', dry_run=True, now=NOW,
                                    fetcher=lambda _: {'jobs': [full_row(company='')]})
        self.assertFalse(errors)
        self.assertEqual(totals['stage.cv_pass'], 1)
        self.assertEqual(totals['stage.eligible'], 0)
        self.assertEqual(totals['review'], 1)
        self.assertFalse(Job.objects.exists())

    def test_company_family_groups_sum_only_their_company_records(self):
        source = dict(api_source('company'), adapter='greenhouse', board='company', company='Example')
        payload = {'jobs': [{'id': 123, 'title': 'Python Developer', 'location': {'name': 'Dubai'},
                             'first_published': (NOW - timedelta(days=1)).isoformat(),
                             'absolute_url': 'https://job-boards.greenhouse.io/company/jobs/123',
                             'content': 'Python Django. 1-3 years experience.'}]}
        totals, reports, errors = run_plan([source], 'python django', dry_run=True, now=NOW, fetcher=lambda _: payload)
        self.assertFalse(errors)
        self.assertEqual(reports['adapter.greenhouse']['eligible'], 1)
        self.assertEqual(reports['adapter.ashby']['fetched'], 0)
        self.assertEqual(totals['eligible'], 1)

    def test_summary_command_shows_groups_totals_but_not_individual_company_rows(self):
        profile = CandidateProfile.objects.create(name='Test', resume_text='python django')
        reports = {'company': {'fetched': 1}, 'adapter.greenhouse': {'fetched': 1},
                   'platform.indeed': {'messages_read': 0}}
        with configuration([api_source()]) as path, patch('jobs.management.commands.discover_all_jobs.run_plan',
                return_value=({'fetched': 1}, reports, [])):
            output = StringIO()
            call_command('discover_all_jobs', sources=path, profile_id=profile.pk, dry_run=True, summary=True, stdout=output)
        self.assertIn('adapter.greenhouse.fetched: 1', output.getvalue())
        self.assertIn('platform.indeed.messages_read: 0', output.getvalue())
        self.assertIn('total.fetched: 1', output.getvalue())
        self.assertNotIn('source.company.', output.getvalue())
