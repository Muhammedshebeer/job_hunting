from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from applications.models import Application
from jobs.models import Job
from jobs.services.discovery import DiscoveryError, discover
from jobs.services.discovery_adapters import ADAPTERS, load_plan, register_adapter, run_plan
from jobs.services.discovery_pipeline import IdentityIndex, classify_record, ingest_rows
from jobs.services.eligibility import eligible
from jobs.tests.test_discovery import NOW, configuration, record
from jobs.tests.test_job_alerts import CONFIG, email_message
from profiles.models import CandidateProfile


def api_source(name='employer_api'):
    return {'name': name, 'adapter': 'authorized_json', 'enabled': True, 'authorized': True,
            'authorization_reference': 'Public API permission covering this usage', 'url': 'https://employer.example/jobs'}


def full_row(**changes):
    return record(description_complete=True, **changes)


class PipelineClassificationTests(SimpleTestCase):
    def test_complete_feed_qualifies_with_explicit_open_status_without_fake_dates(self):
        data, outcome = classify_record(full_row(), 'employer_api', 'python django', NOW, method='authorized_json')
        self.assertEqual(outcome, 'qualified')
        self.assertFalse(data['requires_review'])
        self.assertTrue(data['accepting_applications'])
        self.assertIsNone(data['discovery_metadata'].get('valid_through'))
        self.assertEqual(data['posted_at'].isoformat(), full_row()['posted_at'])

    def test_missing_date_company_location_description_or_experience_needs_review(self):
        for changes, reason in (({'posted_at': None}, 'publication_date_unverified'),
                                ({'company': ''}, 'missing_company'), ({'location': ''}, 'missing_location'),
                                ({'description': ''}, 'missing_description'),
                                ({'description': 'Python Django', 'min_experience': None, 'max_experience': None}, 'experience_unverified')):
            with self.subTest(changes=changes):
                data, outcome = classify_record(full_row(**changes), 'employer_api', 'python django', NOW, method='authorized_json')
                self.assertEqual(outcome, 'review')
                self.assertIn(reason, data['review_reasons'])
                self.assertFalse(data['accepting_applications'])
                self.assertFalse(eligible(Job(**data))[0])

    def test_unknown_description_completeness_and_open_status_require_review(self):
        row = full_row()
        row.pop('description_complete')
        row.pop('accepting_applications')
        data, outcome = classify_record(row, 'api', 'python django', NOW, method='mapped_json')
        self.assertEqual(outcome, 'review')
        self.assertIn('description_completeness_unverified', data['review_reasons'])
        self.assertIn('accepting_applications_unverified', data['review_reasons'])

    def test_strict_existing_filters_are_preserved(self):
        for changes, cv, expected in (({'title': 'Accountant'}, 'python django', 'rejected_role'),
                    ({'location': 'Remote Europe'}, 'python django', 'qualified'),
                    ({'posted_at': (NOW - timedelta(days=11)).isoformat()}, 'python django', 'rejected_date'),
                    ({'posted_at': (NOW + timedelta(seconds=1)).isoformat()}, 'python django', 'rejected_date'),
                    ({'min_experience': 4, 'max_experience': 8}, 'python django', 'rejected_experience'),
                    ({}, 'unrelated', 'rejected_score'),
                    ({'accepting_applications': False}, 'python django', 'rejected_closed')):
            with self.subTest(expected=expected):
                self.assertEqual(classify_record(full_row(**changes), 'api', cv, NOW, method='authorized_json')[1], expected)

    def test_bad_fields_urls_and_dates_do_not_become_qualified(self):
        for changes, expected in (({'url': 'http://example.com'}, 'invalid_url'),
                                  ({'title': []}, 'invalid_fields'), ({'company': 'x' * 251}, 'invalid_fields')):
            self.assertEqual(classify_record(full_row(**changes), 'api', 'python django', NOW, method='authorized_json')[1], expected)
        for date in ('2026-11-09', 'yesterday', '2026-11-09T12:00:00'):
            data, outcome = classify_record(full_row(posted_at=date), 'api', 'python django', NOW, method='authorized_json')
            self.assertEqual(outcome, 'review')
            self.assertIsNone(data['posted_at'])


class PlanValidationTests(SimpleTestCase):
    def test_authorization_required_for_all_adapters(self):
        for adapter in ADAPTERS:
            with configuration([dict(api_source(), adapter=adapter, authorized=False)]) as path:
                with self.assertRaises(DiscoveryError):
                    load_plan(path)

    def test_bad_duplicate_names_unknown_code_and_bad_mappings_are_rejected(self):
        sources = [api_source(), api_source()]
        for config in (sources, [dict(api_source(), name='unsafe name')],
                       [dict(api_source(), adapter='arbitrary.module.loader')],
                       [dict(api_source(), adapter='mapped_json', fields={'url': '__import__(x)'})]):
            with configuration(config) as path, self.assertRaises(DiscoveryError):
                load_plan(path)

    def test_relative_paths_resolve_against_manifest(self):
        with configuration([dict(api_source(), adapter='csv', path='jobs.csv')]) as path:
            self.assertEqual(load_plan(path)[0]['path'], str(Path(path).parent / 'jobs.csv'))

    def test_registered_adapter_extension_requires_explicit_python_code(self):
        register_adapter('synthetic_test', lambda source, context: iter([]), lambda source: None)
        try:
            with configuration([dict(api_source(), adapter='synthetic_test')]) as path:
                self.assertEqual(load_plan(path)[0]['adapter'], 'synthetic_test')
            with self.assertRaises(ValueError):
                register_adapter('synthetic_test', lambda s, c: [], lambda s: None)
        finally:
            ADAPTERS.pop('synthetic_test')


class PipelineIntegrationTests(TestCase):
    def test_dry_run_executes_no_database_mutations(self):
        with CaptureQueriesContext(connection) as queries:
            run_plan([api_source()], 'python django', dry_run=True, now=NOW,
                     fetcher=lambda _: {'jobs': [full_row(), full_row(source_id='2', url='https://example.com/job/2', posted_at=None)]})
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CREATE'))
                             for query in queries))

    def test_existing_numeric_native_id_and_malformed_ids_do_not_break_duplicate_detection(self):
        ingest_rows([full_row(source_id=123)], 'api', 'python django', method='authorized_json', now=NOW)
        counts = ingest_rows([full_row(source_id=123, url='https://other.example/123')], 'api', 'python django', method='authorized_json', now=NOW, dry_run=True)
        self.assertEqual(counts['duplicates'], 1)
        counts = ingest_rows([full_row(source_id=['invalid'], url='https://other.example/456')], 'api', 'python django', method='authorized_json', now=NOW, dry_run=True)
        self.assertEqual(counts['eligible'], 1)

    def test_cross_source_dry_run_deduplicates_in_memory_without_any_writes(self):
        sources = [api_source('one'), api_source('two')]
        totals, reports, errors = run_plan(sources, 'python django', dry_run=True, now=NOW,
                                         fetcher=lambda _: {'jobs': [full_row()]})
        self.assertFalse(errors)
        self.assertEqual(totals['would_create'], 1)
        self.assertEqual(reports['two']['duplicates'], 1)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_persist_qualifying_and_review_records_without_updating_existing_data(self):
        rows = [full_row(), full_row(source_id='2', url='https://example.com/job/2', posted_at=None)]
        counts = ingest_rows(rows, 'api', 'python django', method='authorized_json', now=NOW)
        self.assertEqual(counts['created'], 2)
        self.assertEqual(counts['eligible'], 1)
        self.assertEqual(counts['review'], 1)
        review = Job.objects.get(source_id='2')
        self.assertTrue(review.requires_review)
        self.assertIsNone(review.posted_at)
        original = Job.objects.get(source_id='job-1')
        counts = ingest_rows([full_row(company='Changed')], 'api', 'python django', method='authorized_json', now=NOW)
        self.assertEqual(counts['duplicates'], 1)
        original.refresh_from_db()
        self.assertEqual(original.company, 'Example')
        self.assertFalse(Application.objects.exists())

    def test_ats_url_aliases_and_platform_ids_match_legacy_database_records(self):
        url = 'https://boards.greenhouse.io/company/jobs/123?gh_src=legacy'
        Job.objects.create(source='legacy', url=url, company='Existing', title='Python', location='Dubai', description='Existing')
        row = full_row(url='https://job-boards.greenhouse.io/company/jobs/123', source_id='other')
        counts = ingest_rows([row], 'newsource', 'python django', method='authorized_json', dry_run=True, now=NOW)
        self.assertEqual(counts['duplicates'], 1)

    def test_legacy_discovery_also_deduplicates_records_created_by_other_sources(self):
        Job.objects.create(source='indeed', source_id='greenhouse:company:123',
                           url='https://job-boards.greenhouse.io/company/jobs/123',
                           company='Existing', title='Python', location='Dubai', description='Existing')
        row = full_row(url='https://boards.greenhouse.io/company/jobs/123', source_id='123')
        counts, errors = discover([api_source()], 'python django', dry_run=True, now=NOW,
                                 fetcher=lambda _: {'jobs': [row]})
        self.assertFalse(errors)
        self.assertEqual(counts['duplicates'], 1)
        self.assertEqual(counts['would_create'], 0)

    def test_explicit_employer_requisition_identity_deduplicates_different_urls(self):
        common = {'employer_domain': 'company.example', 'employer_job_id': 'REQ-123'}
        index = IdentityIndex()
        one = ingest_rows([full_row(**common)], 'one', 'python django', method='authorized_json', dry_run=True, index=index, now=NOW)
        two = ingest_rows([full_row(url='https://other.example/job/123', **common)], 'two', 'python django', method='authorized_json', dry_run=True, index=index, now=NOW)
        self.assertEqual(one['would_create'], 1)
        self.assertEqual(two['duplicates'], 1)

    def test_mapped_api_adapter_reads_explicit_paths_and_keeps_unknown_values_in_review(self):
        source = dict(api_source(), adapter='mapped_json', items_path='data.vacancies', fields={
            'title': 'role', 'url': 'link', 'company': 'employer.name', 'location': 'city'})
        payload = {'data': {'vacancies': [{'role': 'Python Developer', 'link': 'https://example.com/job/1',
                                         'employer': {'name': 'Employer'}, 'city': 'Dubai'}]}}
        totals, _, errors = run_plan([source], 'python django', dry_run=True, now=NOW, fetcher=lambda _: payload)
        self.assertFalse(errors)
        self.assertEqual(totals['review'], 1)
        self.assertEqual(totals['publication_date_unverified'], 1)

    def test_source_failure_isolated_and_diagnostics_preserved(self):
        def fetcher(url):
            if url == 'https://failed.example/jobs':
                raise DiscoveryError('PRIVATE response')
            return {'jobs': [full_row()]}
        sources = [dict(api_source('failed'), url='https://failed.example/jobs'), api_source('good')]
        totals, reports, errors = run_plan(sources, 'python django', dry_run=True, now=NOW, fetcher=fetcher)
        self.assertEqual(errors, ['failed'])
        self.assertEqual(reports['failed']['failed_sources'], 1)
        self.assertEqual(reports['good']['eligible'], 1)
        self.assertNotIn('PRIVATE', repr(reports))

    def test_partial_source_failure_keeps_diagnostics_and_continues(self):
        def loader(source, context):
            yield full_row()
            raise DiscoveryError('PRIVATE response')
        register_adapter('partial_test', loader, lambda source: None)
        try:
            source = dict(api_source(), adapter='partial_test')
            totals, reports, errors = run_plan([source, api_source('other')], 'python django', dry_run=True, now=NOW,
                                             fetcher=lambda _: {'jobs': [full_row(url='https://other.example/job/2')]})
            self.assertEqual(errors, ['employer_api'])
            self.assertEqual(reports['employer_api']['would_create'], 1)
            self.assertEqual(totals['would_create'], 2)
        finally:
            ADAPTERS.pop('partial_test')

    def test_short_csv_row_still_produces_review_record(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'short.csv'
            path.write_text('source,url,min_experience,max_experience,accepting_applications,description_complete\n'
                            'indeed,https://www.indeed.com/viewjob?jk=abc123def456\n', encoding='utf-8')
            totals, _, errors = run_plan([dict(api_source(), adapter='csv', path=str(path))],
                                        'python django', dry_run=True, now=NOW)
            self.assertFalse(errors)
            self.assertEqual(totals['review'], 1)

    def test_csv_unknown_fields_review_and_no_platform_page_is_fetched(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'manual.csv'
            path.write_text('source,url,title,location\nindeed,https://www.indeed.com/viewjob?jk=abc123def456,Python Developer,Dubai\n', encoding='utf-8')
            source = dict(api_source(), adapter='csv', path=str(path))
            with patch('socket.socket', side_effect=AssertionError('No network permitted')):
                totals, _, errors = run_plan([source], 'python django', dry_run=True, now=NOW)
            self.assertFalse(errors)
            self.assertEqual(totals['review'], 1)
            self.assertFalse(Job.objects.exists())

    def test_combined_mailbox_adapter_keeps_sender_checks_and_deduplicates_feed(self):
        url = 'https://www.linkedin.com/jobs/view/1234567890/'
        feed = full_row(url=url)
        with configuration(CONFIG) as mailbox_path:
            source = dict(api_source('alerts'), adapter='email_alerts', path=mailbox_path)
            with patch('jobs.services.discovery_adapters.iter_alert_messages', return_value=(item for item in [('linkedin', email_message())])):
                totals, reports, errors = run_plan([api_source(), source], 'python django', dry_run=True, now=NOW, fetcher=lambda _: {'jobs': [feed]})
        self.assertFalse(errors)
        self.assertEqual(totals['would_create'], 1)
        self.assertEqual(reports['alerts']['duplicates'], 1)
        self.assertFalse(Application.objects.exists())

    def test_unknown_opaque_manual_links_are_rejected_without_guessing_identity(self):
        row = {'source': 'naukrigulf', 'url': 'https://www.naukrigulf.com/service/user/action?token=PRIVATE'}
        counts = ingest_rows([row], 'manual', 'python', method='manual_links', platform_rows=True, dry_run=True, now=NOW)
        self.assertEqual(counts['unverified_job_link'], 1)


class Phase2CCommandTests(TestCase):
    def setUp(self):
        self.profile = CandidateProfile.objects.create(name='Test', resume_text='PRIVATE python django')

    def test_combined_command_dry_run_aggregate_output(self):
        with configuration([api_source()]) as path, patch('jobs.services.discovery_adapters.fetch_json'):
            with patch('jobs.management.commands.discover_all_jobs.run_plan', return_value=({'fetched': 2}, {'api': {'fetched': 2}}, [])):
                output = StringIO()
                call_command('discover_all_jobs', sources=path, profile_id=self.profile.pk, dry_run=True, stdout=output)
        self.assertIn('source.api.fetched: 2', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())
        self.assertFalse(Job.objects.exists())

    def test_manual_bare_link_command_creates_only_review_candidate_and_dry_run_is_readonly(self):
        output = StringIO()
        arguments = {'platform': 'linkedin', 'url': 'https://www.linkedin.com/jobs/view/1234567890/',
                     'profile_id': self.profile.pk, 'stdout': output}
        call_command('import_job_link', dry_run=True, **arguments)
        self.assertFalse(Job.objects.exists())
        call_command('import_job_link', **arguments)
        self.assertTrue(Job.objects.get().requires_review)
        self.assertEqual(Job.objects.get().company, '')
        self.assertFalse(Application.objects.exists())

    def test_safe_csv_entrypoint_uses_insert_only_and_requires_authorization_reference(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'jobs.csv'
            path.write_text('source,url\nbayt,https://www.bayt.com/en/uae/jobs/example-1234567/\n', encoding='utf-8')
            output = StringIO()
            call_command('import_jobs', str(path), profile_id=self.profile.pk,
                         authorization_reference='Personal manual entry', dry_run=True, stdout=output)
            self.assertIn('review: 1', output.getvalue())
            self.assertFalse(Job.objects.exists())
            with self.assertRaises(CommandError):
                call_command('import_jobs', str(path), profile_id=self.profile.pk, authorization_reference='', stdout=StringIO())
