"""Synthetic URL structures from redacted local evidence, not private URLs."""
from collections import Counter
from urllib.parse import quote
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.job_alerts import import_alert_messages, job_identity, parse_alert
from jobs.services.link_diagnostics import embedded_shapes, naukri_route_kind
from jobs.tests.test_discovery import NOW
from jobs.tests.test_job_alerts import email_message


def wrapper(destination):
    return 'https://alerts.naukrigulf.com/?url=' + quote(destination, safe='')


class VerifiedRouteFamiliesTests(SimpleTestCase):
    def test_observed_three_segment_user_route_is_not_a_verified_job(self):
        # Unknown static segment values are deliberately synthetic.
        destination = 'https://www.naukrigulf.com/service/user/action?session=PRIVATE'
        url = wrapper(destination)
        self.assertIsNone(job_identity(url, 'naukrigulf'))
        shapes = list(embedded_shapes(url, 'naukrigulf'))
        self.assertEqual(shapes[0][2], '/<segment>/user/<segment>')
        self.assertEqual(shapes[0][4], 'user_service_route_without_verified_job_identity')
        self.assertNotIn('PRIVATE', repr(shapes))

    def test_observed_mailer_settings_route_is_not_job_detail(self):
        destination = 'https://www.naukrigulf.com/mnj/account/settings/mailer/preferences?token=PRIVATE'
        self.assertIsNone(job_identity(wrapper(destination), 'naukrigulf'))
        self.assertEqual(list(embedded_shapes(wrapper(destination), 'naukrigulf'))[0][4],
                         'mailer_settings_not_job_detail')

    def test_search_and_editorial_routes_are_identified_but_not_accepted_as_jobs(self):
        for path, reason in (('/search/results/example', 'search_results_route'),
                             ('/career-tips/category/example', 'editorial_or_career_advice_route')):
            self.assertEqual(naukri_route_kind(path), reason)
            self.assertIsNone(job_identity(wrapper('https://www.naukrigulf.com' + path), 'naukrigulf'))

    def test_unknown_three_segment_route_stays_unknown_not_a_guessed_search_or_job(self):
        self.assertIsNone(naukri_route_kind('/alpha/beta/gamma'))
        self.assertIsNone(job_identity(wrapper('https://www.naukrigulf.com/alpha/beta/gamma'), 'naukrigulf'))

    def test_verified_naukri_job_path_is_still_decoded_without_network(self):
        destination = 'https://www.naukrigulf.com/python-developer-jobs-in-dubai-jid-101126000123'
        with patch('socket.socket', side_effect=AssertionError('Network prohibited')):
            self.assertEqual(job_identity(wrapper(destination), 'naukrigulf'), job_identity(destination, 'naukrigulf'))

    def test_query_ids_do_not_turn_user_or_mailer_routes_into_jobs(self):
        for path in ('/service/user/action', '/mnj/account/settings/mailer/preferences'):
            self.assertIsNone(job_identity('https://www.naukrigulf.com' + path + '?jid=101126000123', 'naukrigulf'))

    def test_indeed_id_stays_stable_across_link_occurrences_and_metadata_is_not_invented(self):
        first = 'https://ae.indeed.com/rc/clk/dl?jk=abc123def456&tracking=PRIVATE'
        second = 'https://ae.indeed.com/rc/clk/dl?jk=abc123def456&tracking=OTHER'
        self.assertEqual(job_identity(first, 'indeed'), job_identity(second, 'indeed'))
        html = '<article><h3>Frontend Developer</h3><a href="' + first + '">View full job description</a></article>'
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], 'Frontend Developer')
        self.assertEqual(row['title_evidence'], 'heading')
        for field in ('company', 'location', 'description'):
            self.assertEqual(row[field], '')


class TargetedRouteDryRunTests(TestCase):
    def test_real_shape_mix_preserves_rejections_and_readonly_review(self):
        service = wrapper('https://www.naukrigulf.com/service/user/action?token=PRIVATE')
        preferences = wrapper('https://www.naukrigulf.com/mnj/account/settings/mailer/preferences')
        direct = 'https://www.naukrigulf.com/python-jobs-in-dubai-jid-101126000123'
        html = (f'<a href="{service}">View recommendations</a><a href="{preferences}">Mail settings</a>'
                '<a href="https://www.facebook.com/example">Social</a>'
                '<a href="https://play.google.com/store/apps/details?id=PRIVATE">App</a>'
                f'<article><h3>Python Developer</h3><a href="{direct}">View job</a></article>')
        diagnostics = Counter()
        counts = import_alert_messages([('naukrigulf', email_message('naukrigulf', html=html))],
                                       'PRIVATE CV python django', dry_run=True, now=NOW, url_diagnostics=diagnostics)
        self.assertEqual(counts['extracted_job_candidates'], 1)
        self.assertEqual(counts['would_create_review'], 1)
        self.assertEqual(counts['rejected_external_links'], 2)
        self.assertEqual(counts['publication_date_unverified'], 1)
        self.assertEqual(counts['experience_unverified'], 1)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())
        self.assertNotIn('PRIVATE', repr(diagnostics))
