import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.discovery import (
    DiscoveryError, NoRedirects, canonical_url, discover, experience,
    fetch_json, load_sources, measure_discovery_row, normalize, posted_within_week, source_rows,
)
from profiles.models import CandidateProfile


NOW = datetime(2026, 11, 9, 12, tzinfo=dt_timezone.utc)
SOURCE = {
    'name': 'indeed', 'adapter': 'authorized_json', 'enabled': True,
    'authorized': True, 'authorization_reference': 'Employer-approved test feed',
    'url': 'https://feeds.example.com/jobs',
}


def record(**changes):
    row = {
        'source_id': 'job-1', 'company': 'Example', 'title': 'Python Django Developer',
        'location': 'Dubai, UAE', 'url': 'https://careers.example.com/jobs/1',
        'posted_at': (NOW - timedelta(days=1)).isoformat(),
        'description': 'Python Django. Required: 1-3 years of experience.',
        'min_experience': 1, 'max_experience': 3, 'accepting_applications': True,
    }
    row.update(changes)
    return row


@contextmanager
def configuration(sources):
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'sources.json'
        path.write_text(json.dumps(sources), encoding='utf-8')
        yield str(path)


class FilteringTests(SimpleTestCase):
    def test_funnel_reports_independent_and_intersected_metrics(self):
        metrics = measure_discovery_row(record(), 'python django', NOW)
        for key in ('fetched', 'recent', 'uae_location', 'recent_uae', 'uae_target_role',
                    'uae_target_cv_pass', 'recent_uae_target_cv_pass'):
            self.assertEqual(metrics[key], 1, key)
        self.assertEqual(metrics['uae_target_experience_unverified'], 0)

    def test_old_jobs_do_not_inflate_recent_cv_matches(self):
        row = record(posted_at=(NOW - timedelta(days=11)).isoformat())
        metrics = measure_discovery_row(row, 'python django', NOW)
        self.assertEqual(metrics['uae_target_cv_pass'], 1)
        self.assertEqual(metrics['recent_uae_target_cv_pass'], 0)
        self.assertEqual(metrics['older_than_publication_window'], 1)

    def test_missing_invalid_old_and_future_dates_are_distinct(self):
        for date, key in ((None, 'missing_publication_date'), ('yesterday', 'invalid_publication_date'),
                          ('2026-11-09T08:00:00', 'invalid_publication_date'),
                          ((NOW + timedelta(days=1)).isoformat(), 'future_publication_date')):
            with self.subTest(date=date):
                metrics = measure_discovery_row(record(posted_at=date), 'python django', NOW)
                self.assertEqual(metrics[key], 1)
                self.assertEqual(metrics['recent'], 0)

    def test_unknown_experience_still_allows_diagnostic_cv_score(self):
        row = record(min_experience=None, max_experience=None, description='Python Django')
        metrics = measure_discovery_row(row, 'python django', NOW)
        self.assertEqual(metrics['recent_uae_target_experience_unverified'], 1)
        self.assertEqual(metrics['recent_uae_target_cv_pass'], 1)
        self.assertEqual(normalize(row, 'test', 'python django', NOW)[1], 'experience_needs_review')

    def test_exact_three_day_boundary_and_offsets(self):
        for value in (NOW, NOW - timedelta(days=3), NOW - timedelta(seconds=1)):
            with self.subTest(value=value):
                self.assertEqual(posted_within_week(value.isoformat(), NOW), value)
        self.assertEqual(posted_within_week('2026-11-09T16:00:00+04:00', NOW), NOW)

    def test_rejects_unverified_old_and_future_dates(self):
        for value in (None, '', 'yesterday', '2026-11-09', '2026-11-09T10:00:00',
                      (NOW + timedelta(microseconds=1)).isoformat(),
                      (NOW - timedelta(days=3, microseconds=1)).isoformat()):
            with self.subTest(value=value):
                self.assertIsNone(posted_within_week(value, NOW))

    def test_target_roles_and_locations(self):
        for title in ('Python Developer', 'Django Developer', 'Backend Engineer',
                      'Back-end Developer', 'Full Stack Developer', 'Fullstack Developer',
                      'Software Engineer', 'AI Developer'):
            for location in ('Dubai', 'Abu Dhabi, UAE', 'Sharjah', 'Remote - UAE', 'Riyadh', 'London', 'Remote - Europe', 'Remote', 'United Arab Emirates'):
                with self.subTest(title=title, location=location):
                    data, reason = normalize(record(title=title, location=location), 'test', 'python django', NOW)
                    self.assertIsNone(reason)
                    self.assertEqual(data['match_score'], 100)

    def test_rejections(self):
        cases = [
            ({'title': 'Accountant'}, 'role'),
            ({'min_experience': 4}, 'experience_needs_review'),
            ({'min_experience': 4, 'max_experience': 6}, 'experience'),
            ({'min_experience': 0, 'max_experience': 0}, 'experience'),
            ({'min_experience': None, 'max_experience': None, 'description': 'Python Django'}, 'experience_needs_review'),
            ({'min_experience': True}, 'experience_needs_review'),
            ({'accepting_applications': False}, 'closed_or_unverified_open'),
            ({'accepting_applications': 'true'}, 'closed_or_unverified_open'),
            ({'url': 'http://example.com'}, 'invalid_url'),
            ({'source_id': ''}, 'missing_source_id'),
            ({'title': 'x' * 251}, 'role'),
            ({'company': 'x' * 251}, 'invalid_fields'),
            ({'description': ''}, 'missing_fields'),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(normalize(record(**changes), 'test', 'python django', NOW)[1], expected)

    def test_score_threshold(self):
        # Django (5) + rest api (3) + Python (4) = 12; 9/12 = 75%.
        row = record(description='Django REST API Python')
        self.assertEqual(normalize(row, 'test', 'django python', NOW)[0]['match_score'], 75)
        self.assertEqual(normalize(row, 'test', 'django', NOW)[0]['match_score'], 42)
        self.assertEqual(normalize(row, 'test', 'unrelated skills', NOW)[1], 'match_below_threshold')

    def test_conservative_experience_extraction(self):
        for text, expected in (
            ('1–3 years of experience', (1, 3)),
            ('2+ years experience', (2, None)),
            ('2 years of experience', (2, 2)),
            ('1-3 years experience; 5 years experience', None),
            ('Competitive salary', None),
        ):
            with self.subTest(text=text):
                self.assertEqual(experience({'description': text}), expected)

    def test_canonicalization_preserves_job_identity(self):
        self.assertEqual(canonical_url('https://EXAMPLE.com/jobs?jk=123&utm_source=indeed&trk=foo'),
                         'https://example.com/jobs?jk=123')
        self.assertNotEqual(canonical_url('https://example.com/?jk=1'), canonical_url('https://example.com/?jk=2'))

    def test_rejects_credential_urls(self):
        for url in ('https://user:pass@example.com', 'https://example.com?api_key=secret'):
            with self.subTest(url=url), self.assertRaises(DiscoveryError):
                canonical_url(url)


class AdapterTests(SimpleTestCase):
    def test_greenhouse_uses_publication_never_update_date(self):
        source = dict(SOURCE, adapter='greenhouse', board='test', company='Example')
        job = {
            'id': 123, 'title': 'Backend Engineer', 'location': {'name': 'Dubai'},
            'absolute_url': 'https://example.com/job/123',
            'updated_at': NOW.isoformat(), 'content': '<p>Python &amp; Django</p>',
        }
        fetcher = MagicMock(side_effect=[{'jobs': [job]}, job])
        row = list(source_rows(source, fetcher))[0]
        self.assertIsNone(row['posted_at'])
        self.assertEqual(row['source_id'], '123')
        self.assertIn('Python & Django', row['description'])
        self.assertEqual(fetcher.call_args_list[0].args[0], 'https://boards-api.greenhouse.io/v1/boards/test/jobs?content=true')
        self.assertEqual(fetcher.call_args_list[1].args[0], 'https://boards-api.greenhouse.io/v1/boards/test/jobs/123')

    def test_greenhouse_detail_supplies_verified_publication(self):
        source = dict(SOURCE, adapter='greenhouse', board='test', company='Example')
        job = {'id': 123, 'title': 'Backend Engineer', 'location': {'name': 'Dubai'},
               'absolute_url': 'https://example.com/123', 'content': 'Python; 1-3 years experience'}
        fetcher = MagicMock(side_effect=[{'jobs': [job]}, dict(job, first_published=NOW.isoformat())])
        row = list(source_rows(source, fetcher))[0]
        self.assertIsNone(normalize(row, 'test', 'python', NOW)[1])

    def test_greenhouse_non_target_jobs_do_not_trigger_detail_requests(self):
        source = dict(SOURCE, adapter='greenhouse', board='test', company='Example')
        fetcher = MagicMock(return_value={'jobs': [{'id': 123, 'title': 'Accountant', 'location': {'name': 'Dubai'}}]})
        list(source_rows(source, fetcher))
        fetcher.assert_called_once()

    def test_malformed_adapter_rows_are_rejected(self):
        for adapter in ('greenhouse', 'ashby'):
            source = dict(SOURCE, adapter=adapter, board='test', company='Example')
            rows = list(source_rows(source, lambda _: {'jobs': [None, {'jobUrl': 123, 'content': {}}]}))
            for row in rows:
                self.assertIsNotNone(normalize(row, 'test', 'python', NOW)[1])

    def test_ashby_uses_publication_and_explicit_remote_workplace(self):
        source = dict(SOURCE, adapter='ashby', board='test', company='Example')
        fetcher = MagicMock(return_value={'jobs': [{
            'title': 'Python Developer', 'location': 'United Arab Emirates',
            'workplaceType': 'Remote', 'isListed': True,
            'jobUrl': 'https://jobs.ashbyhq.com/test/abc',
            'publishedAt': NOW.isoformat(), 'descriptionPlain': 'Python. 1-3 years experience',
        }]})
        row = list(source_rows(source, fetcher))[0]
        self.assertEqual(row['source_id'], 'abc')
        self.assertIsNone(normalize(row, 'test', 'python', NOW)[1])

    def test_bad_envelopes_fail_and_bad_records_are_returned_for_rejection(self):
        with self.assertRaises(DiscoveryError):
            list(source_rows(SOURCE, lambda _: {'results': []}))
        self.assertEqual(list(source_rows(SOURCE, lambda _: {'jobs': [None]})), [None])

    def test_authorization_required_before_fetch(self):
        for changes in ({'authorized': False}, {'authorization_reference': ''}, {'adapter': 'scraper'},
                        {'url': 'http://example.com'}, {'url': 'https://example.com?token=secret'}):
            with self.subTest(changes=changes), configuration([dict(SOURCE, **changes)]) as path:
                with self.assertRaises(DiscoveryError):
                    load_sources(path)

    def test_disabled_board_requires_no_credentials(self):
        with configuration([{'name': 'linkedin', 'enabled': False}]) as path:
            self.assertFalse(load_sources(path)[0]['enabled'])

    def test_malformed_configuration_fails_cleanly(self):
        for sources in ({}, [None], [dict(SOURCE, authorization_reference=None)],
                        [dict(SOURCE, adapter=[])], [SOURCE, SOURCE],
                        [dict(SOURCE, adapter='ashby', board=None)]):
            with self.subTest(sources=sources), configuration(sources) as path:
                with self.assertRaises(DiscoveryError):
                    load_sources(path)

    def test_greenhouse_detail_requests_are_bounded(self):
        source = dict(SOURCE, adapter='greenhouse', board='test', company='Example')
        jobs = [{'id': n, 'title': 'Python Developer', 'location': {'name': 'Dubai'}} for n in range(101)]
        calls = []
        def fetcher(url):
            calls.append(url)
            if len(calls) == 1:
                return {'jobs': jobs}
            return dict(jobs[int(url.rsplit('/', 1)[-1])], first_published=NOW.isoformat())
        with self.assertRaisesMessage(DiscoveryError, 'detail request limit'):
            list(source_rows(source, fetcher))
        self.assertEqual(len(calls), 101)  # One list plus 100 detail requests.

    def test_network_failures_are_redacted_and_not_retried(self):
        failures = [URLError('sensitive server data'), HTTPError('secret', 429, 'secret', {}, None)]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), patch('jobs.services.discovery.build_opener') as opener:
                opener.return_value.open.side_effect = failure
                with self.assertRaises(DiscoveryError) as raised:
                    fetch_json('https://example.com/feed')
                self.assertNotIn('secret', str(raised.exception))
                self.assertNotIn('sensitive', str(raised.exception))
                opener.return_value.open.assert_called_once()

    def test_redirects_refused(self):
        with self.assertRaises(DiscoveryError):
            NoRedirects().redirect_request(None, None, 302, '', {}, 'https://other.example.com')

    def test_http_body_size_and_json_validation(self):
        for body, content_type in ((b'x' * 5_000_001, 'application/json'),
                                   (b'invalid', 'application/json'), (b'{}', 'text/html')):
            with self.subTest(content_type=content_type, size=len(body)), patch('jobs.services.discovery.build_opener') as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.headers.get_content_type.return_value = content_type
                response.read.return_value = body
                with self.assertRaises(DiscoveryError):
                    fetch_json('https://example.com/jobs')


class DiscoveryPersistenceTests(TestCase):
    def run_discovery(self, rows, **options):
        return discover([SOURCE], 'python django', now=NOW, fetcher=lambda _: {'jobs': rows}, **options)

    def test_creates_job_without_touching_applications_or_profiles(self):
        counts, errors = self.run_discovery([record()])
        self.assertFalse(errors)
        self.assertEqual(counts['created'], 1)
        self.assertEqual(Job.objects.get().match_score, 100)
        self.assertEqual(Application.objects.count(), 0)
        self.assertEqual(CandidateProfile.objects.count(), 0)

    def test_per_source_funnel_totals(self):
        counts, _ = self.run_discovery([record(), record(source_id='2', posted_at=None)])
        self.assertEqual(counts['fetched'], 2)
        self.assertEqual(counts['source.indeed.fetched'], 2)
        self.assertEqual(counts['recent'], 1)
        self.assertEqual(counts['source.indeed.recent'], 1)
        self.assertEqual(counts['missing_publication_date'], 1)

    def test_repeat_discovery_preserves_existing_records(self):
        self.run_discovery([record()])
        counts, _ = self.run_discovery([record(title='Backend Engineer')])
        self.assertEqual(counts['duplicates'], 1)
        self.assertEqual(Job.objects.get().title, 'Python Django Developer')

    def test_url_and_source_identity_deduplication(self):
        counts, _ = self.run_discovery([
            record(), record(source_id='other', url=record()['url'] + '?utm_source=test'),
            record(url='https://example.com/another'),
        ])
        self.assertEqual(counts['created'], 1)
        self.assertEqual(counts['duplicates'], 2)

    def test_cross_source_url_deduplication(self):
        other = dict(SOURCE, name='bayt')
        counts, _ = discover([SOURCE, other], 'python django', now=NOW, fetcher=lambda _: {'jobs': [record()]})
        self.assertEqual(counts['created'], 1)
        self.assertEqual(counts['duplicates'], 1)

    def test_legacy_tracking_urls_are_preserved_and_deduplicated(self):
        self.run_discovery([record()])
        job = Job.objects.get()
        job.url += '?utm_source=legacy'
        job.save(update_fields=['url'])
        counts, _ = self.run_discovery([record(source_id='different')])
        self.assertEqual(counts['duplicates'], 1)
        self.assertTrue(Job.objects.get().url.endswith('?utm_source=legacy'))

    def test_dry_run_writes_nothing_and_counts_batch_duplicates(self):
        counts, _ = self.run_discovery([record(), record()], dry_run=True)
        self.assertEqual(counts['would_create'], 1)
        self.assertEqual(counts['duplicates'], 1)
        self.assertFalse(Job.objects.exists())

    def test_source_failure_isolated_and_disabled_sources_never_fetched(self):
        sources = [SOURCE, dict(SOURCE, name='good', url='https://good.example.com'),
                   dict(SOURCE, name='disabled', enabled=False)]
        def fetcher(url):
            if url == SOURCE['url']:
                raise DiscoveryError('HTTP 403; no retry or bypass attempted')
            return {'jobs': [record()]}
        counts, errors = discover(sources, 'python django', now=NOW, fetcher=fetcher)
        self.assertEqual(counts['created'], 1)
        self.assertEqual(counts['failed_sources'], 1)
        self.assertEqual(counts['disabled_sources'], 1)
        self.assertEqual(errors[0][0], 'indeed')

    def test_bad_row_does_not_discard_good_rows(self):
        counts, _ = self.run_discovery([None, record(posted_at=None), record()])
        self.assertEqual(counts['invalid_record'], 1)
        self.assertEqual(counts['unverified_or_outside_publication_window'], 1)
        self.assertEqual(counts['created'], 1)

    def test_command_uses_existing_text_and_dry_run(self):
        profile = CandidateProfile.objects.create(name='Test', resume='private/resumes/existing.pdf', resume_text='python django')
        with configuration([SOURCE]) as path, patch('jobs.management.commands.discover_jobs.discover') as service:
            service.return_value = ({'would_create': 1}, [])
            output = StringIO()
            call_command('discover_jobs', sources=path, profile_id=profile.pk, dry_run=True, stdout=output)
            service.assert_called_once_with([SOURCE], 'python django', dry_run=True)
            self.assertIn('no writes', output.getvalue())
        profile.refresh_from_db()
        self.assertEqual(profile.resume.name, 'private/resumes/existing.pdf')
        self.assertEqual(Application.objects.count(), 0)

    def test_command_fails_for_no_sources_missing_profile_or_resume(self):
        with configuration([]) as path, self.assertRaisesMessage(CommandError, 'No enabled'):
            call_command('discover_jobs', sources=path, profile_id=1)
        with configuration([SOURCE]) as path:
            with self.assertRaisesMessage(CommandError, 'profile not found'):
                call_command('discover_jobs', sources=path, profile_id=123)
            profile = CandidateProfile.objects.create(name='No text', resume='existing.pdf')
            with self.assertRaisesMessage(CommandError, 'resume_text is required'):
                call_command('discover_jobs', sources=path, profile_id=profile.pk)

    def test_partial_failure_command_returns_error(self):
        profile = CandidateProfile.objects.create(name='Test', resume_text='python')
        with configuration([SOURCE]) as path, patch('jobs.management.commands.discover_jobs.discover') as service:
            service.return_value = ({'failed_sources': 1}, [('indeed', 'HTTP 429')])
            with self.assertRaisesMessage(CommandError, 'One or more sources failed'):
                call_command('discover_jobs', sources=path, profile_id=profile.pk, stdout=StringIO(), stderr=StringIO())

    def test_full_command_with_mocked_http_preserves_candidate_and_applications(self):
        profile = CandidateProfile.objects.create(name='Test', resume='private/resumes/existing.pdf', resume_text='python django')
        with configuration([SOURCE]) as path, patch('jobs.services.discovery.build_opener') as opener, patch('jobs.services.discovery.timezone.now', return_value=NOW):
            response = opener.return_value.open.return_value.__enter__.return_value
            response.headers.get_content_type.return_value = 'application/json'
            response.read.return_value = json.dumps({'jobs': [record()]}).encode()
            call_command('discover_jobs', sources=path, profile_id=profile.pk, dry_run=True, stdout=StringIO())
            self.assertFalse(Job.objects.exists())
            call_command('discover_jobs', sources=path, profile_id=profile.pk, stdout=StringIO())
            call_command('discover_jobs', sources=path, profile_id=profile.pk, stdout=StringIO())
        self.assertEqual(Job.objects.count(), 1)
        self.assertFalse(Application.objects.exists())
        profile.refresh_from_db()
        self.assertEqual(profile.notice_days, 60)
        self.assertTrue(profile.employment_visa)
        self.assertTrue(profile.needs_transfer)
        self.assertEqual(profile.resume_text, 'python django')
        self.assertEqual(profile.resume.name, 'private/resumes/existing.pdf')
