"""Redacted diagnostic/URL regressions; all addresses and tokens are synthetic."""
from collections import Counter
from io import StringIO
from urllib.parse import quote
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.job_alerts import import_alert_messages, job_identity, record_link
from jobs.services.link_diagnostics import embedded_shapes, url_shape
from jobs.tests.test_discovery import NOW, configuration
from jobs.tests.test_job_alerts import CONFIG, email_message
from profiles.models import CandidateProfile


GREENHOUSE = 'https://job-boards.greenhouse.io/example/jobs/123456'
ASHBY = 'https://jobs.ashbyhq.com/example/12345678-1234-1234-1234-123456789abc'


class URLRegressionTests(SimpleTestCase):
    def test_observed_indeed_download_route_with_existing_job_id(self):
        identity = job_identity('https://ae.indeed.com/rc/clk/dl?jk=abc123456def&tracking=PRIVATE', 'indeed')
        self.assertEqual(identity, ('https://www.indeed.com/viewjob?jk=abc123456def', 'abc123456def'))
        for url in ('https://ae.indeed.com/rc/clk/dl?tracking=PRIVATE',
                    'https://ae.indeed.com/rc/clk/dl?jk=bad!',
                    'https://ae.indeed.com/rc/clk/dl/other?jk=abc123456def'):
            self.assertIsNone(job_identity(url, 'indeed'))

    def test_naukri_observed_wrapper_structure_without_job_identity_is_not_accepted(self):
        # Same observed shape: root wrapper -> three-segment same-platform
        # destination, with no independently recognizable job-detail route.
        destination = 'https://www.naukrigulf.com/section/action/target'
        wrapper = 'https://alerts.naukrigulf.com/?url=' + quote(destination, safe='')
        self.assertIsNone(job_identity(wrapper, 'naukrigulf'))
        self.assertEqual(len(list(embedded_shapes(wrapper, 'naukrigulf'))), 1)

    def test_naukri_explicit_supported_job_destination_decodes_without_request(self):
        destination = 'https://www.naukrigulf.com/python-jobs-in-dubai-jid-101126000123'
        wrapper = 'https://alerts.naukrigulf.com/?url=' + quote(destination, safe='')
        with patch('socket.socket', side_effect=AssertionError('No network permitted')):
            self.assertEqual(job_identity(wrapper, 'naukrigulf'), job_identity(destination, 'naukrigulf'))

    def test_exact_public_ats_detail_routes_preserve_namespace(self):
        for source in ('indeed', 'naukrigulf', 'linkedin', 'bayt'):
            with self.subTest(source=source):
                self.assertEqual(job_identity(GREENHOUSE + '?gh_src=PRIVATE', source),
                                 (GREENHOUSE, 'greenhouse:example:123456'))
                self.assertEqual(job_identity(ASHBY, source),
                                 (ASHBY, 'ashby:example:12345678-1234-1234-1234-123456789abc'))
                self.assertEqual(job_identity(GREENHOUSE.replace('job-boards.', 'boards.'), source)[0], GREENHOUSE)

    def test_platform_wrapper_can_contain_explicit_approved_ats_destination(self):
        for destination in (GREENHOUSE, ASHBY):
            wrapped = 'https://www.naukrigulf.com/?url=' + quote(destination, safe='')
            self.assertEqual(job_identity(wrapped, 'naukrigulf'), job_identity(destination, 'naukrigulf'))

    def test_employer_homepages_ats_homepages_spoofed_hosts_and_credentials_stay_rejected(self):
        for url in ('https://careers.example.com/jobs/123',
                    'https://job-boards.greenhouse.io/example',
                    'https://jobs.ashbyhq.com/example',
                    GREENHOUSE.replace('greenhouse.io', 'greenhouse.io.evil.test'),
                    ASHBY + '/apply', GREENHOUSE.replace('https://', 'http://'),
                    GREENHOUSE.replace('https://', 'https://user:PRIVATE@'),
                    'https://unknown.example/redirect?url=' + quote(GREENHOUSE, safe='')):
            with self.subTest(url=url):
                self.assertIsNone(job_identity(url, 'naukrigulf'))

    def test_live_external_host_categories_remain_non_job_links(self):
        for host, category in (('play.google.com', 'app_store'), ('apps.apple.com', 'app_store'),
                               ('www.facebook.com', 'social_site'), ('www.instagram.com', 'social_site'),
                               ('twitter.com', 'social_site')):
            url = 'https://' + host + '/PRIVATE?token=PRIVATE'
            self.assertIsNone(job_identity(url, 'naukrigulf'))
            self.assertEqual(url_shape(url, 'naukrigulf')[3], category)

    def test_diagnostics_never_contain_original_url_query_tokens_or_path_values(self):
        url = 'https://personal-PRIVATE.indeed.com/PRIVATE/abcPRIVATE123456789012345678901234567890?jk=PRIVATE&email=private@example.com'
        diagnostics, counts = Counter(), Counter()
        record_link(url, 'indeed', counts, diagnostics)
        output = repr(diagnostics)
        for private in ('PRIVATE', 'private@example.com', 'https://', '?jk', 'email='):
            self.assertNotIn(private, output)
        self.assertEqual(sum(diagnostics.values()), 1)

    def test_diagnostics_are_off_by_default(self):
        counts = Counter()
        record_link('https://ae.indeed.com/rc/clk/dl?jk=abc123456def', 'indeed', counts)
        self.assertFalse(any('URL_SHAPE' in key or 'host' in key for key in counts))


class DiagnosticCommandTests(TestCase):
    def test_diagnostics_require_dry_run_before_accessing_mailbox(self):
        with patch('jobs.management.commands.import_job_alerts.iter_alert_messages') as mailbox:
            with self.assertRaisesMessage(CommandError, 'requires --dry-run'):
                call_command('import_job_alerts', profile_id=1, diagnose_links=True, stdout=StringIO())
            mailbox.assert_not_called()

    def test_opt_in_command_output_is_redacted_and_database_is_unchanged(self):
        profile = CandidateProfile.objects.create(name='Test', resume_text='PRIVATE CV python django')
        url = 'https://ae.indeed.com/rc/clk/dl?jk=abc123456def&token=PRIVATE'
        html = f'<div><a href="{url}">Python Developer</a><p>Company: Example</p><p>Location: Dubai</p></div>'
        messages = (item for item in [('indeed', email_message('indeed', html=html))])
        with configuration(CONFIG) as path, patch('jobs.management.commands.import_job_alerts.iter_alert_messages', return_value=messages):
            output = StringIO()
            call_command('import_job_alerts', config=path, profile_id=profile.pk, dry_run=True,
                         diagnose_links=True, stdout=output)
        text = output.getvalue()
        self.assertIn('URL_SHAPE', text)
        self.assertIn('path=/rc/clk/dl', text)
        for private in ('PRIVATE', 'abc123456def', 'https://', 'token=', 'Company: Example'):
            self.assertNotIn(private, text)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_approved_ats_candidates_keep_origin_and_review_gate_and_deduplicate_legacy_records(self):
        html = f'<a href="{GREENHOUSE}">Python Developer</a>'
        counts = import_alert_messages([('naukrigulf', email_message('naukrigulf', html=html))],
                                       'python django', now=NOW)
        self.assertEqual(counts['created_review'], 1)
        job = Job.objects.get()
        self.assertEqual(job.source, 'naukrigulf')
        self.assertEqual(job.source_id, 'greenhouse:example:123456')
        self.assertIsNone(job.posted_at)
        self.assertFalse(job.accepting_applications)
        self.assertTrue(job.requires_review)
        job.source = 'company_greenhouse'
        job.url = GREENHOUSE.replace('job-boards.', 'boards.') + '?gh_src=PRIVATE'
        job.save()
        counts = import_alert_messages([('indeed', email_message('indeed', html=html))], 'python', now=NOW, dry_run=True)
        self.assertEqual(counts['duplicates'], 1)
        self.assertEqual(Job.objects.count(), 1)
        self.assertFalse(Application.objects.exists())
