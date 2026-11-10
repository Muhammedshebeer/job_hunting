import json
from datetime import datetime, timedelta, timezone as utc
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from applications.models import Application
from jobs.models import Job
from jobs.services.date_diagnostics import assess_date, candidate_date_diagnostic, date_window, mapped_publication_evidence
from jobs.services.discovery import DiscoveryError, source_rows
from jobs.services.discovery_adapters import run_plan
from jobs.services.discovery_pipeline import classify_record
from jobs.tests.test_discovery import NOW, configuration
from jobs.tests.test_job_alerts import email_message, posting
from jobs.tests.test_phase2c import api_source, full_row
from profiles.models import CandidateProfile


def mapped_source(field='published_at', documented=True):
    source = dict(api_source(), adapter='mapped_json', fields={
        'url': 'url', 'source_id': 'source_id', 'title': 'title', 'company': 'company',
        'location': 'location', 'posted_at': field, 'description': 'description',
        'min_experience': 'min_experience', 'max_experience': 'max_experience',
        'description_complete': 'description_complete', 'accepting_applications': 'accepting_applications'})
    if documented:
        source.update(publication_date_semantics='original_publication',
                      publication_date_reference='Provider documentation: published_at is initial publication')
    return source


class DateAssessmentTests(SimpleTestCase):
    def test_missing_invalid_date_only_and_naive_are_distinct_unknowns(self):
        for value, reason in ((None, 'missing_publication_date'), ('', 'missing_publication_date'),
                              ('yesterday', 'invalid_publication_date'), (123456789, 'invalid_publication_date'),
                              ('2026-11-09', 'ambiguous_date_only'),
                              ('2026-11-09T12:00:00', 'ambiguous_timezone'),
                              ('2026-20-40T12:00:00Z', 'invalid_publication_date')):
            with self.subTest(value=value):
                self.assertEqual(assess_date(value, NOW), (None, reason))

    def test_240_hour_boundary_is_same_in_utc_and_uae(self):
        uae = ZoneInfo('Asia/Dubai')
        boundary = NOW - timedelta(days=10)
        self.assertEqual(assess_date(boundary.astimezone(uae).isoformat(), NOW)[1], 'within_publication_window')
        self.assertEqual(assess_date((boundary - timedelta(microseconds=1)).isoformat(), NOW)[1], 'older_than_publication_window')
        self.assertEqual(assess_date((NOW + timedelta(microseconds=1)).isoformat(), NOW)[1], 'future_publication_date')
        self.assertEqual(assess_date(NOW.isoformat(), NOW)[1], 'within_publication_window')

    def test_uae_calendar_day_does_not_replace_rolling_window(self):
        now = datetime(2026, 11, 9, 20, 30, tzinfo=utc.utc)  # Nov 10, 00:30 UAE.
        window = date_window(now)
        self.assertTrue(window['run_uae'].startswith('2026-11-10T00:30'))
        self.assertTrue(window['cutoff_uae'].startswith('2026-10-31T00:30'))
        self.assertEqual(window['inclusive_window_hours'], 240)
        self.assertEqual(assess_date('2026-10-31T00:29:59+04:00', now)[1], 'older_than_publication_window')

    def test_greenhouse_update_date_never_replaces_old_original_publication(self):
        source = dict(api_source(), adapter='greenhouse', board='test', company='Employer')
        payload = {'jobs': [{'id': 1, 'title': 'Python Developer', 'location': {'name': 'Dubai'},
                             'first_published': '2020-01-01T12:00:00Z', 'updated_at': NOW.isoformat(),
                             'absolute_url': 'https://example.com/job/1', 'content': 'Python Django'}]}
        row = list(source_rows(source, lambda _: payload))[0]
        diagnostic = candidate_date_diagnostic(row, source, NOW)
        self.assertEqual(diagnostic['publication_field'], 'first_published')
        self.assertEqual(diagnostic['publication_semantics'], 'original_publication')
        self.assertEqual(diagnostic['date_result'], 'older_than_publication_window')
        self.assertTrue(diagnostic['parsed_utc'].startswith('2020-01-01'))

    def test_greenhouse_missing_publication_stays_missing_even_in_detail(self):
        source = dict(api_source(), adapter='greenhouse', board='test', company='Employer')
        job = {'id': 1, 'title': 'Python Developer', 'location': {'name': 'Dubai'},
               'updated_at': NOW.isoformat(), 'absolute_url': 'https://example.com/job/1',
               'content': 'Python Django. 1-3 years experience.'}
        responses = iter([{'jobs': [job]}, job])
        row = list(source_rows(source, lambda _: next(responses)))[0]
        self.assertIsNone(row['posted_at'])
        data, outcome = classify_record(row, source['name'], 'python django', NOW, method='greenhouse', complete_description=True)
        self.assertEqual(outcome, 'review')
        self.assertIn('publication_date_unverified', data['review_reasons'])

    def test_ashby_explicit_new_publication_is_allowed_but_modified_timestamp_is_not_fallback(self):
        source = dict(api_source(), adapter='ashby', board='test', company='Employer')
        row = {'id': '1', 'title': 'Python Developer', 'location': 'Dubai', 'isListed': True,
               'jobUrl': 'https://example.com/job/1', 'descriptionPlain': 'Python Django. 1-3 years experience.',
               'publishedAt': NOW.isoformat(), 'createdAt': '2020-01-01T12:00:00Z', 'modified_at': NOW.isoformat()}
        normalized = list(source_rows(source, lambda _: {'jobs': [row]}))[0]
        self.assertEqual(candidate_date_diagnostic(normalized, source, NOW)['date_result'], 'within_publication_window')
        self.assertEqual(candidate_date_diagnostic(normalized, source, NOW)['publication_semantics'], 'last_publication')
        row.pop('publishedAt')
        normalized = list(source_rows(source, lambda _: {'jobs': [row]}))[0]
        self.assertIsNone(normalized['posted_at'])

    def test_diagnostic_is_whitelisted_and_sanitizes_email_urls_and_controls(self):
        row = full_row(company='Employer private@example.com', title='Python Developer https://secret.example/token', location='Dubai\nUAE')
        row.update(description='PRIVATE BODY', oauth_token='PRIVATE TOKEN')
        diagnostic = candidate_date_diagnostic(row, api_source(), NOW)
        text = json.dumps(diagnostic)
        for private in ('private@example.com', 'https://', 'PRIVATE BODY', 'PRIVATE TOKEN'):
            self.assertNotIn(private, text)
        self.assertEqual(diagnostic['location'], 'Dubai UAE')

    def test_diagnostic_includes_only_approved_role_and_uae_candidates(self):
        self.assertIsNone(candidate_date_diagnostic(full_row(title='Accountant'), api_source(), NOW))
        self.assertIsNone(candidate_date_diagnostic(full_row(location='London'), api_source(), NOW))


class MappedDateEvidenceTests(TestCase):
    def test_documented_publication_field_can_qualify(self):
        row = full_row(published_at=NOW.isoformat())
        totals, _, errors = run_plan([mapped_source()], 'python django', dry_run=True, now=NOW,
                                    fetcher=lambda _: {'jobs': [row]})
        self.assertFalse(errors)
        self.assertEqual(totals['eligible'], 1)

    def test_unknown_semantics_are_review_not_automatically_outdated(self):
        row = full_row(published_at='2020-01-01T12:00:00Z')
        diagnostics = []
        totals, _, errors = run_plan([mapped_source(documented=False)], 'python django', dry_run=True,
                                    now=NOW, fetcher=lambda _: {'jobs': [row]}, date_diagnostics=diagnostics)
        self.assertFalse(errors)
        self.assertEqual(totals['review'], 1)
        self.assertEqual(totals['rejected_date'], 0)
        self.assertEqual(totals['stage.date_unknown'], 1)
        self.assertEqual(diagnostics[0]['date_result'], 'publication_semantics_unverified')
        self.assertIsNone(diagnostics[0]['parsed_utc'])

    def test_update_modified_fetched_email_and_created_fields_cannot_qualify_even_if_relabelled(self):
        for field in ('updated_at', 'metadata.modifiedAt', 'fetched_at', 'received_at', 'INTERNALDATE', 'created_at', 'lastModifiedDate'):
            with self.subTest(field=field):
                source = mapped_source(field)
                self.assertEqual(mapped_publication_evidence(source)[2], 'modification_field_not_publication')
                row = full_row()
                if field.startswith('metadata.'):
                    row['metadata'] = {'modifiedAt': NOW.isoformat()}
                else:
                    row[field] = NOW.isoformat()
                totals, _, errors = run_plan([source], 'python django', dry_run=True, now=NOW, fetcher=lambda _: {'jobs': [row]})
                self.assertFalse(errors)
                self.assertEqual(totals['review'], 1)
                self.assertEqual(totals['stage.date_pass'], 0)
                self.assertEqual(totals['modification_field_not_publication'], 1)

    def test_known_old_documented_publication_stays_rejected(self):
        row = full_row(published_at='2020-01-01T12:00:00Z', updated_at=NOW.isoformat())
        totals, _, _ = run_plan([mapped_source()], 'python django', dry_run=True, now=NOW, fetcher=lambda _: {'jobs': [row]})
        self.assertEqual(totals['rejected_date'], 1)
        self.assertEqual(totals['review'], 0)

    def test_email_received_time_does_not_supply_missing_publication(self):
        from jobs.services.job_alerts import parse_alert
        message = email_message(html='<script type="application/ld+json">' + json.dumps(posting(datePosted=None)) + '</script>')
        row = list(parse_alert(message, 'linkedin'))[0]
        data, outcome = classify_record(row, 'linkedin', 'python django', NOW, method='email_alert')
        self.assertEqual(outcome, 'review')
        self.assertIsNone(data['posted_at'])
        self.assertEqual(candidate_date_diagnostic(row, {'name': 'alerts', 'adapter': 'email_alerts'}, NOW)['date_result'], 'missing_publication_date')

    def test_unknown_date_stays_review_only_if_other_known_checks_pass(self):
        row = full_row(title='Accountant', published_at=NOW.isoformat())
        totals, _, _ = run_plan([mapped_source(documented=False)], 'python django', dry_run=True, now=NOW, fetcher=lambda _: {'jobs': [row]})
        self.assertEqual(totals['rejected_role'], 1)
        self.assertEqual(totals['review'], 0)


class DateDiagnosticCommandTests(TestCase):
    def test_command_requires_dry_run_before_mailbox_or_database_work(self):
        with patch('jobs.management.commands.discover_all_jobs.load_plan') as loader:
            with self.assertRaisesMessage(CommandError, 'requires --dry-run'):
                call_command('discover_all_jobs', profile_id=1, diagnose_dates=True, stdout=StringIO())
            loader.assert_not_called()

    def test_programmatic_diagnostics_also_require_readonly_mode(self):
        with self.assertRaises(DiscoveryError):
            run_plan([], 'python', date_diagnostics=[])

    def test_live_style_diagnostic_command_uses_one_clock_and_whitelisted_output(self):
        profile = CandidateProfile.objects.create(name='Test', resume_text='PRIVATE CV python django')
        source = mapped_source()
        row = full_row(published_at='2020-01-01T12:00:00Z')
        with configuration([source]) as path, patch('jobs.management.commands.discover_all_jobs.timezone.now', return_value=NOW):
            def runner(sources, cv_text, **kwargs):
                return run_plan(sources, cv_text, fetcher=lambda _: {'jobs': [row]}, **kwargs)
            with patch('jobs.management.commands.discover_all_jobs.run_plan', side_effect=runner), CaptureQueriesContext(connection) as queries:
                output = StringIO()
                call_command('discover_all_jobs', sources=path, profile_id=profile.pk, dry_run=True,
                             summary=True, diagnose_dates=True, stdout=output)
        self.assertIn('DATE_WINDOW', output.getvalue())
        self.assertIn('DATE_CANDIDATE', output.getvalue())
        self.assertIn('older_than_publication_window', output.getvalue())
        self.assertNotIn('PRIVATE CV', output.getvalue())
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())
