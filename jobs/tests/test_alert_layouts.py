"""Synthetic alert fixtures: no real mailbox data, URLs visited, or credentials."""
import json
from collections import Counter
from email import policy
from email.parser import BytesParser
from urllib.parse import quote
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.job_alerts import import_alert_messages, job_identity, parse_alert
from jobs.tests.test_discovery import NOW
from jobs.tests.test_job_alerts import URLS, email_message, posting


DIRECT = {
    'linkedin': 'https://www.linkedin.com/comm/jobs/view/python-developer-1234567890/',
    'indeed': 'https://ae.indeed.com/viewjob?jk=abc123def456',
    'naukrigulf': 'https://www.naukrigulf.com/python-jobs-in-dubai-jid-091126000123',
    'bayt': 'https://www.bayt.com/en/uae/jobs/python-developer-1234567/',
}


def wrapped(source, destination=None):
    host = {'linkedin': 'www.linkedin.com', 'indeed': 'indeedemail.com',
            'naukrigulf': 'www.naukrigulf.com', 'bayt': 'www.bayt.com'}[source]
    return f'https://{host}/email/redirect?url=' + quote(destination or DIRECT[source], safe='')


def html_fixture(source):
    # Product-style tables, nested text spans, semantic company/location fields,
    # and title + CTA links for the same job are common layout conventions.
    link = wrapped(source)
    return f'''<html><body><table><tr><td><div>
      <a href="{link}"><span>Python</span><span> Developer</span></a>
      <div class="company-name">Example Employer</div>
      <span class="job-location">Dubai, UAE</span>
      <p>Posted 2 days ago</p><a href="{link}">View job</a>
    </div></td></tr></table>
    <a href="https://example.invalid/unsubscribe?token=PRIVATE">Unsubscribe</a>
    </body></html>'''


class AlertLayoutTests(SimpleTestCase):
    def test_four_platform_html_fixtures_with_safe_embedded_destinations(self):
        for source in DIRECT:
            with self.subTest(source=source):
                counters = Counter()
                message = email_message(source, html=html_fixture(source))
                rows = list(parse_alert(message, source, counters))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['source'], source)
                self.assertEqual(rows[0]['company'], 'Example Employer')
                self.assertEqual(rows[0]['location'], 'Dubai, UAE')
                self.assertIn('Python', rows[0]['title'])
                self.assertEqual(rows[0]['url'], job_identity(DIRECT[source], source)[0])
                self.assertEqual(counters['recognized_job_links'], 2)
                self.assertEqual(counters['decoded_tracking_links'], 2)
                self.assertEqual(counters['extracted_job_candidates'], 1)
                self.assertEqual(counters['rejected_external_links'], 1)
                self.assertEqual(rows[0]['posted_at'], '')  # Relative date not promoted.

    def test_four_platform_plain_text_fixtures(self):
        for source in DIRECT:
            with self.subTest(source=source):
                text = f'Python Developer\nExample Employer - Sharjah, UAE\n{wrapped(source)}'
                row = list(parse_alert(email_message(source, text=text), source))[0]
                self.assertEqual(row['company'], 'Example Employer')
                self.assertEqual(row['location'], 'Sharjah, UAE')
                self.assertEqual(row['title'], 'Python Developer')

    def test_plain_text_still_used_when_html_contains_no_recognized_links(self):
        text = 'Python Developer\nCompany: Employer\nLocation: Dubai\n' + DIRECT['linkedin']
        message = email_message(text=text, html='<p>Open the app to read your alert</p>')
        counters = Counter()
        rows = list(parse_alert(message, 'linkedin', counters))
        self.assertEqual(len(rows), 1)
        self.assertEqual(counters['html_parts'], 1)
        self.assertEqual(counters['plain_text_parts'], 1)

    def test_multipart_wire_encoding_and_alternative_duplicates(self):
        text = 'Python Developer\nCompany: Employer\nLocation: Dubai\n' + DIRECT['linkedin']
        message = email_message(text=text, html=html_fixture('linkedin'))
        message = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        self.assertEqual(len(list(parse_alert(message, 'linkedin'))), 1)

    def test_multiple_text_jobs_without_blank_lines_and_without_metadata_bleed(self):
        for url_first in (False, True):
            with self.subTest(url_first=url_first):
                parts = []
                for title, company, location, identity in (
                    ('Python Developer', 'First', 'Dubai', '111'),
                    ('Backend Engineer', 'Second', 'Abu Dhabi', '222'),
                ):
                    url = f'https://www.linkedin.com/jobs/view/{identity}/'
                    metadata = f'Title: {title}\nCompany: {company}\nLocation: {location}'
                    parts.append(url + '\n' + metadata if url_first else metadata + '\n' + url)
                rows = list(parse_alert(email_message(text='\n'.join(parts)), 'linkedin'))
                self.assertEqual(len(rows), 2)
                self.assertEqual({row['source_id']: (row['company'], row['location']) for row in rows},
                                 {'111': ('First', 'Dubai'), '222': ('Second', 'Abu Dhabi')})

    def test_multiple_html_jobs_and_duplicate_ctas_do_not_share_fields(self):
        html = '<table>'
        for number, company, location in (('111', 'First', 'Dubai'), ('222', 'Second', 'Abu Dhabi')):
            url = wrapped('linkedin', f'https://www.linkedin.com/jobs/view/{number}/')
            html += f'<tr><td><h3>Python Developer</h3><a href="{url}">View job</a><p>Company: {company}</p><p>Location: {location}</p></td></tr>'
        html += '</table>'
        rows = list(parse_alert(email_message(html=html), 'linkedin'))
        self.assertEqual({row['source_id']: row['company'] for row in rows}, {'111': 'First', '222': 'Second'})

    def test_url_first_digest_header_is_not_mistaken_for_job_metadata(self):
        text = ('Your job alert for Python Developer\n'
                'https://www.linkedin.com/jobs/view/111/\nTitle: Python Developer\nCompany: First\nLocation: Dubai\n'
                'https://www.linkedin.com/jobs/view/222/\nTitle: Backend Engineer\nCompany: Second\nLocation: Sharjah')
        rows = list(parse_alert(email_message(text=text), 'linkedin'))
        self.assertEqual({row['source_id']: row['company'] for row in rows}, {'111': 'First', '222': 'Second'})

    def test_safe_tracking_is_bounded_and_rejects_external_destinations(self):
        good = wrapped('linkedin')
        self.assertEqual(job_identity(good, 'linkedin'), job_identity(DIRECT['linkedin'], 'linkedin'))
        for url in (
            wrapped('linkedin', 'https://evil.example/jobs/view/123/'),
            wrapped('linkedin', 'https://www.linkedin.com.evil.example/jobs/view/123/'),
            wrapped('linkedin', 'https://user:secret@www.linkedin.com/jobs/view/123/'),
            wrapped('linkedin', 'https://www.indeed.com/viewjob?jk=abc123456'),
            wrapped('linkedin', 'javascript:alert(1)'),
            'https://unknown.example/redirect?url=' + quote(DIRECT['linkedin'], safe=''),
            'https://www.linkedin.com/email/redirect?token=OPAQUE',
        ):
            with self.subTest(url=url):
                self.assertIsNone(job_identity(url, 'linkedin'))
        for _ in range(6):
            good = wrapped('linkedin', good)
        self.assertIsNone(job_identity(good, 'linkedin'))

    def test_opaque_links_produce_diagnostics_not_invented_jobs(self):
        html = '<a href="https://indeedemail.com/email/click?token=PRIVATE">Python Developer</a>'
        counters = Counter()
        self.assertEqual(list(parse_alert(email_message('indeed', html=html), 'indeed', counters)), [])
        self.assertEqual(counters['recognized_job_links'], 0)
        self.assertEqual(counters['unresolved_tracking_links'], 1)
        self.assertNotIn('PRIVATE', repr(counters))

    def test_parser_never_opens_network_links(self):
        with patch('socket.socket', side_effect=AssertionError('No network permitted')):
            for source in DIRECT:
                self.assertEqual(len(list(parse_alert(email_message(source, html=html_fixture(source)), source))), 1)

    def test_attached_multipart_subtree_is_not_parsed(self):
        message = email_message(html='<p>No jobs</p>')
        attached = email_message()
        attached['Content-Disposition'] = 'attachment'
        message.make_mixed()
        message.attach(attached)
        self.assertEqual(list(parse_alert(message, 'linkedin')), [])

    def test_better_title_link_wins_over_image_link_for_same_job(self):
        html = '<div><a href="' + DIRECT['linkedin'] + '"><img src="https://example.invalid/image"></a><a href="' + DIRECT['linkedin'] + '">Python Developer</a><p>Company: Employer</p><p>Location: Dubai</p></div>'
        rows = list(parse_alert(email_message(html=html), 'linkedin'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Python Developer')


class AlertDiagnosticImportTests(TestCase):
    def test_unknown_dates_experience_and_descriptions_remain_readonly_review_candidates(self):
        messages = [(source, email_message(source, html=html_fixture(source))) for source in DIRECT]
        counts = import_alert_messages(messages, 'PRIVATE CV python django', now=NOW, dry_run=True)
        self.assertEqual(counts['extracted_job_candidates'], 4)
        self.assertEqual(counts['would_create_review'], 4)
        self.assertEqual(counts['publication_date_unverified'], 4)
        self.assertEqual(counts['experience_unverified'], 4)
        self.assertEqual(counts['description_completeness_unverified'], 4)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())
        self.assertNotIn('PRIVATE', repr(counts))

    def test_missing_fields_counted_even_for_candidates_rejected_by_location(self):
        html = '<a href="' + DIRECT['linkedin'] + '">Python Developer</a>'
        counts = import_alert_messages([('linkedin', email_message(html=html))], 'python', now=NOW, dry_run=True)
        self.assertEqual(counts['candidate_missing_company'], 1)
        self.assertEqual(counts['candidate_missing_location'], 1)
        self.assertEqual(counts['candidate_missing_description'], 1)

    def test_actual_posting_date_rule_still_applies_after_parser_changes(self):
        value = posting(datePosted='2020-01-01T12:00:00+00:00')
        html = '<script type="application/ld+json">' + json.dumps(value) + '</script>'
        counts = import_alert_messages([('linkedin', email_message(html=html))], 'python django', now=NOW, dry_run=True)
        self.assertEqual(counts['rejected_date'], 1)
        self.assertEqual(counts['would_create_qualified'], 0)
