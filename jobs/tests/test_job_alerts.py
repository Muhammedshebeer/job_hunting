import json
from collections import Counter
from datetime import timedelta
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.alert_mailbox import MailboxError, identify_source, iter_alert_messages, load_mailbox_config, secure_keyring
from jobs.services.eligibility import eligible
from jobs.services.job_alerts import classify_alert, import_alert_messages, job_identity, parse_alert, parse_publication
from jobs.tests.test_discovery import NOW, configuration
from profiles.models import CandidateProfile


CONFIG = {
    'authorized': True, 'username': 'test@example.com', 'host': 'imap.gmail.com',
    'folder': 'UAEJobAlerts', 'credential_service': 'test-mailbox',
    'trusted_authserv_id': 'mx.google.com', 'lookback_days': 14, 'max_messages': 10,
    'senders': {'linkedin': ['alerts@linkedin.com'], 'indeed': ['alerts@indeed.com'],
                'naukrigulf': ['alerts@naukrigulf.com'], 'bayt': ['alerts@bayt.com']},
}
URLS = {
    'linkedin': 'https://www.linkedin.com/jobs/view/python-developer-1234567890/?trackingId=private',
    'indeed': 'https://ae.indeed.com/rc/clk?jk=abc123def456&utm_source=mail',
    'naukrigulf': 'https://www.naukrigulf.com/python-developer-jobs-in-dubai-1-to-3-years-jid-091126000123',
    'bayt': 'https://www.bayt.com/en/uae/jobs/python-developer-1234567/',
}


def posting(source='linkedin', **changes):
    value = {
        '@type': 'JobPosting', 'title': 'Python Django Developer',
        'url': URLS[source], 'hiringOrganization': {'name': 'Example Employer'},
        'jobLocation': {'address': {'addressLocality': 'Dubai', 'addressCountry': 'UAE'}},
        'datePosted': (NOW - timedelta(days=1)).isoformat(),
        'validThrough': (NOW + timedelta(days=10)).isoformat(),
        'description': '<p>Python Django. Requires 1-3 years of experience.</p>',
    }
    value.update(changes)
    return value


def email_message(source='linkedin', html=None, text=None):
    message = EmailMessage()
    message['From'] = f'Alerts <alerts@{source}.com>'
    message['Subject'] = 'New jobs matching your preferences'
    message['Date'] = 'Mon, 09 Nov 2026 12:00:00 +0000'
    message['Message-ID'] = '<private-message-id@example.com>'
    message['Authentication-Results'] = f'mx.google.com; dmarc=pass header.from={source}.com'
    message.set_content(text or 'Job alert')
    if html is None and text is None:
        html = '<script type="application/ld+json">' + json.dumps(posting(source)) + '</script>'
    if html is not None:
        message.add_alternative(html, subtype='html')
    return message


class ParsingTests(SimpleTestCase):
    def test_all_platform_sources_and_ids(self):
        for source in URLS:
            with self.subTest(source=source):
                message = email_message(source)
                self.assertEqual(identify_source(message, CONFIG), source)
                rows = list(parse_alert(message, source))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['company'], 'Example Employer')
                self.assertEqual(rows[0]['location'], 'Dubai, UAE')
                self.assertNotIn('private', rows[0]['url'])
                data, outcome = classify_alert(rows[0], 'python django', NOW)
                self.assertEqual(outcome, 'qualified')
                self.assertFalse(data['requires_review'])

    def test_source_rejects_spoofed_unselected_or_unauthenticated_senders(self):
        for header, value in (
            ('From', 'alerts@linkedin.com.evil.example'),
            ('From', 'other@linkedin.com'),
            ('Authentication-Results', 'evil.example; dmarc=pass header.from=linkedin.com'),
            ('Authentication-Results', 'mx.google.com; dmarc=fail header.from=linkedin.com'),
            ('Subject', 'Your password was changed'),
        ):
            with self.subTest(header=header, value=value):
                message = email_message()
                message.replace_header(header, value)
                self.assertIsNone(identify_source(message, CONFIG))

    def test_tracking_urls_deduplicate_and_bad_urls_rejected(self):
        canonical, identity = job_identity(URLS['linkedin'], 'linkedin')
        self.assertEqual(identity, '1234567890')
        self.assertEqual(job_identity(canonical, 'linkedin'), (canonical, identity))
        self.assertEqual(job_identity('https://www.indeed.com/viewjob?jk=abc123def456', 'indeed'),
                         job_identity(URLS['indeed'], 'indeed'))
        for url in ('https://www.linkedin.com.evil.test/jobs/view/123/',
                    'https://www.linkedin.com/jobs/search', 'http://www.linkedin.com/jobs/view/123/',
                    'https://user:secret@www.linkedin.com/jobs/view/123/',
                    'https://www.linkedin.com:444/jobs/view/123/',
                    'https://www.linkedin.com/redir?url=https://evil.test'):
            self.assertIsNone(job_identity(url, 'linkedin'))

    def test_multi_card_fields_do_not_bleed_between_jobs(self):
        html = '''<html><body><table><tr><td>
        <a href="https://www.linkedin.com/jobs/view/111/">Python Developer</a>
        <p>Company: First</p><p>Location: Dubai</p></td></tr><tr><td>
        <a href="https://www.linkedin.com/jobs/view/222/">Backend Engineer</a>
        <p>Company: Second</p><p>Location: London</p></td></tr></table></body></html>'''
        rows = list(parse_alert(email_message(html=html), 'linkedin'))
        self.assertEqual([(row['company'], row['location']) for row in rows], [('First', 'Dubai'), ('Second', 'London')])

    def test_card_and_plain_text_parsing(self):
        text = ('Title: Python Developer\nCompany: Employer\nLocation: Dubai\n'
                'Date posted: 2026-11-08T12:00:00+00:00\nExperience: 1-3 years experience\n'
                'Description: Python Django\nhttps://www.linkedin.com/jobs/view/111/')
        row = list(parse_alert(email_message(text=text), 'linkedin'))[0]
        self.assertEqual(row['title'], 'Python Developer')
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertIn('description_completeness_unverified', data['review_reasons'])
        self.assertFalse(data['accepting_applications'])

    def test_common_bullet_layout_extracts_company_and_location(self):
        html = '<div><a href="https://www.linkedin.com/jobs/view/123/">Python Developer</a><p>Employer \u2022 Dubai, UAE</p></div>'
        row = list(parse_alert(email_message(html=html), 'linkedin'))[0]
        self.assertEqual(row['company'], 'Employer')
        self.assertEqual(row['location'], 'Dubai, UAE')

    def test_does_not_store_footer_or_recipient_in_card_excerpt(self):
        html = '<div><a href="https://www.linkedin.com/jobs/view/123/">Python Developer</a><p>Company: Employer</p><p>Location: Dubai</p><p>Sent to private@example.com</p></div>'
        row = list(parse_alert(email_message(html=html), 'linkedin'))[0]
        self.assertNotIn('private@example.com', row['description'])

    def test_attachments_and_malformed_structured_data_ignored(self):
        message = email_message(html='<script type="application/ld+json">not json</script>')
        message.add_attachment(b'private', maintype='application', subtype='pdf', filename='cv.pdf')
        self.assertEqual(list(parse_alert(message, 'linkedin')), [])

    def test_does_not_use_email_date_as_posting_date(self):
        message = email_message(html='<script type="application/ld+json">' + json.dumps(posting(datePosted=None)) + '</script>')
        row = list(parse_alert(message, 'linkedin'))[0]
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertIsNone(data['posted_at'])
        self.assertIn('publication_date_unverified', data['review_reasons'])

    def test_date_validation_boundary_old_future_naive_and_relative(self):
        for value in ('2026-11-09', 'yesterday', '2 days ago', '2026-11-09T08:00:00', None):
            self.assertIsNone(parse_publication(value))
        for date, expected in ((NOW - timedelta(days=3), 'qualified'),
                               (NOW - timedelta(days=3, seconds=1), 'rejected_date'),
                               (NOW + timedelta(seconds=1), 'rejected_date')):
            message = email_message(html='<script type="application/ld+json">' + json.dumps(posting(datePosted=date.isoformat())) + '</script>')
            row = list(parse_alert(message, 'linkedin'))[0]
            self.assertEqual(classify_alert(row, 'python django', NOW)[1], expected)

    def test_missing_fields_experience_and_open_status_require_review(self):
        value = posting(hiringOrganization={}, jobLocation={}, description='Python Django', validThrough=None)
        message = email_message(html='<script type="application/ld+json">' + json.dumps(value) + '</script>')
        data, outcome = classify_alert(list(parse_alert(message, 'linkedin'))[0], 'python django', NOW)
        self.assertEqual(outcome, 'review')
        for reason in ('missing_company', 'missing_location', 'experience_unverified', 'accepting_applications_unverified'):
            self.assertIn(reason, data['review_reasons'])
        self.assertFalse(eligible(Job(**data))[0])

    def test_date_only_is_retained_for_review_without_midnight_invention(self):
        value = posting(datePosted='2026-11-08')
        row = list(parse_alert(email_message(html='<script type="application/ld+json">' + json.dumps(value) + '</script>'), 'linkedin'))[0]
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertIsNone(data['posted_at'])
        self.assertEqual(data['discovery_metadata']['publication_date_text'], '2026-11-08')

    def test_malformed_structured_fields_are_not_trusted(self):
        value = posting(title={'private': 'value'}, hiringOrganization={'name': ['wrong']})
        row = list(parse_alert(email_message(html='<script type="application/ld+json">' + json.dumps(value) + '</script>'), 'linkedin'))[0]
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertEqual(data['title'], '')
        self.assertEqual(data['company'], '')

    def test_target_role_location_experience_and_score_filters(self):
        for changes, cv, expected in (
            ({'title': 'Accountant'}, 'python django', 'rejected_role'),
            ({'jobLocation': {'address': {'addressLocality': 'London'}}}, 'python django', 'qualified'),
            ({'description': 'Python Django. 5 years experience'}, 'python django', 'rejected_experience'),
            ({}, 'unrelated skills', 'rejected_score'),
        ):
            value = posting(**changes)
            row = list(parse_alert(email_message(html='<script type="application/ld+json">' + json.dumps(value) + '</script>'), 'linkedin'))[0]
            self.assertEqual(classify_alert(row, cv, NOW)[1], expected)

    def test_explicit_uae_remote_qualifies(self):
        value = posting(jobLocation={}, jobLocationType='TELECOMMUTE', applicantLocationRequirements={'name': 'United Arab Emirates'})
        row = list(parse_alert(email_message(html='<script type="application/ld+json">' + json.dumps(value) + '</script>'), 'linkedin'))[0]
        self.assertEqual(classify_alert(row, 'python django', NOW)[1], 'qualified')


class MailboxTests(SimpleTestCase):
    def test_plaintext_or_unknown_credential_backends_are_refused(self):
        with patch('keyring.get_keyring', return_value=object()):
            with self.assertRaisesMessage(MailboxError, 'plaintext credential stores refused'):
                secure_keyring()

    def test_explicit_authorization_and_safe_configuration(self):
        for changes in ({'authorized': False}, {'senders': {}}, {'host': 'https://user:secret@example.com'},
                        {'senders': {'linkedin': ['alerts@evil.example']}}, {'folder': 'x\r\nINBOX'}, {'max_messages': True}):
            with self.subTest(changes=changes), configuration(dict(CONFIG, **changes)) as path:
                with self.assertRaises(MailboxError):
                    load_mailbox_config(path)
        with configuration(CONFIG) as path:
            self.assertEqual(load_mailbox_config(path), CONFIG)

    def mailbox_client(self, message=None):
        client = MagicMock()
        client.login.return_value = ('OK', [])
        client.select.return_value = ('OK', [])
        raw = (message or email_message()).as_bytes()
        def uid(operation, *args):
            if operation == 'search':
                return 'OK', [b'1']
            if 'HEADER.FIELDS' in args[-1]:
                return 'OK', [(b'1 (RFC822.SIZE 10000 BODY[HEADER] {1000}', raw)]
            return 'OK', [(b'1 (BODY[] {1000}', raw)]
        client.uid.side_effect = uid
        return client

    def test_readonly_peek_and_no_mailbox_mutations(self):
        client = self.mailbox_client()
        counts = Counter()
        self.assertEqual(len(list(iter_alert_messages(CONFIG, counts, password='test', client_factory=lambda *a, **k: client))), 1)
        client.select.assert_called_once_with('"UAEJobAlerts"', readonly=True)
        fetches = [call.args[-1] for call in client.uid.call_args_list if call.args[0] == 'fetch']
        self.assertTrue(all('BODY.PEEK[' in value for value in fetches))
        client.store.assert_not_called()
        client.expunge.assert_not_called()
        client.logout.assert_called_once()

    def test_untrusted_or_non_alert_headers_never_fetch_body(self):
        message = email_message()
        message.replace_header('Subject', 'Your account password changed')
        client = self.mailbox_client(message)
        counts = Counter()
        self.assertEqual(list(iter_alert_messages(CONFIG, counts, password='test', client_factory=lambda *a, **k: client)), [])
        self.assertEqual(counts['untrusted_or_unselected_sender'], 1)
        self.assertFalse(any(call.args[-1] == '(BODY.PEEK[])' for call in client.uid.call_args_list))

    def test_credentials_and_server_responses_are_redacted(self):
        client = self.mailbox_client()
        client.login.side_effect = RuntimeError('PRIVATE PASSWORD AND RESPONSE')
        with self.assertRaises(MailboxError) as raised:
            list(iter_alert_messages(CONFIG, Counter(), password='PRIVATE', client_factory=lambda *a, **k: client))
        self.assertNotIn('PRIVATE', str(raised.exception))
        client.logout.assert_called_once()

    def test_missing_password_never_connects(self):
        factory = MagicMock()
        with self.assertRaises(MailboxError):
            list(iter_alert_messages(CONFIG, Counter(), password='', client_factory=factory))
        factory.assert_not_called()

    def test_oversized_message_never_fetches_body(self):
        client = self.mailbox_client()
        original = client.uid.side_effect
        def uid(operation, *args):
            if operation == 'fetch':
                return 'OK', [(b'1 (RFC822.SIZE 9000000', b'')]
            return original(operation, *args)
        client.uid.side_effect = uid
        counts = Counter()
        self.assertEqual(list(iter_alert_messages(CONFIG, counts, password='test', client_factory=lambda *a, **k: client)), [])
        self.assertEqual(counts['oversized_or_unknown_size'], 1)


class ImportTests(TestCase):
    def test_daily_preparation_cannot_reserve_an_application_for_review_jobs(self):
        profile = CandidateProfile.objects.create(name='Test', resume='private/resumes/existing.pdf', resume_text='python django')
        import_alert_messages([('linkedin', email_message())], profile.resume_text, now=NOW)
        job = Job.objects.get()
        job.requires_review = True
        job.save(update_fields=['requires_review'])
        with patch('django.utils.timezone.now', return_value=NOW):
            call_command('run_daily_jobs', profile_id=profile.pk, limit=20, stdout=StringIO())
        self.assertFalse(Application.objects.exists())

    def test_save_qualified_and_review_jobs_never_creates_applications(self):
        messages = [('linkedin', email_message()), ('indeed', email_message('indeed', html='<a href="' + URLS['indeed'] + '">Python Developer</a>'))]
        counts = import_alert_messages(messages, 'python django', now=NOW)
        self.assertEqual(counts['created_qualified'], 1)
        self.assertEqual(counts['created_review'], 1)
        review = Job.objects.get(source='indeed')
        self.assertTrue(review.requires_review)
        self.assertFalse(eligible(review)[0])
        self.assertFalse(Application.objects.exists())

    def test_repeated_batch_and_existing_tracking_urls_deduplicate(self):
        counts = import_alert_messages([('linkedin', email_message()), ('linkedin', email_message())], 'python django', now=NOW)
        self.assertEqual(counts['duplicates'], 1)
        job = Job.objects.get()
        job.url = URLS['linkedin']
        job.source_id = 'legacy-id'
        job.save()
        counts = import_alert_messages([('linkedin', email_message())], 'python django', now=NOW)
        self.assertEqual(counts['duplicates'], 1)
        self.assertEqual(Job.objects.get().source_id, 'legacy-id')

    def test_existing_csv_source_alias_still_deduplicates_platform_job_id(self):
        import_alert_messages([('linkedin', email_message())], 'python django', now=NOW)
        job = Job.objects.get()
        job.source = 'linkedin_csv'
        job.url = URLS['linkedin']
        job.save()
        counts = import_alert_messages([('linkedin', email_message())], 'python django', now=NOW)
        self.assertEqual(counts['duplicates'], 1)
        self.assertEqual(Job.objects.count(), 1)

    def test_dry_run_never_writes(self):
        counts = import_alert_messages([('linkedin', email_message())], 'python django', dry_run=True, now=NOW)
        self.assertEqual(counts['would_create_qualified'], 1)
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_store_missing_fields_without_inventing_values(self):
        message = email_message(html='<a href="https://www.linkedin.com/jobs/view/123/">View job</a>')
        import_alert_messages([('linkedin', message)], 'python django', now=NOW)
        job = Job.objects.get()
        self.assertEqual(job.title, '')
        self.assertEqual(job.company, '')
        self.assertIsNone(job.posted_at)
        self.assertTrue(job.requires_review)

    def test_command_uses_profile_text_and_only_aggregate_output(self):
        profile = CandidateProfile.objects.create(name='Test', resume_text='PRIVATE python django')
        with configuration(CONFIG) as path, patch('jobs.management.commands.import_job_alerts.iter_alert_messages', return_value=iter([('linkedin', email_message())])) as mailbox:
            # A generator is used by production so it can always be closed.
            mailbox.return_value = (item for item in [('linkedin', email_message())])
            output = StringIO()
            with patch('jobs.services.job_alerts.timezone.now', return_value=NOW):
                call_command('import_job_alerts', config=path, profile_id=profile.pk, dry_run=True, stdout=output)
            self.assertIn('would_create_qualified: 1', output.getvalue())
            self.assertNotIn('PRIVATE', output.getvalue())
            self.assertNotIn('private-message-id', output.getvalue())
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_command_requires_config_and_profile_before_mailbox_connection(self):
        with configuration(CONFIG) as path, patch('jobs.management.commands.import_job_alerts.iter_alert_messages') as mailbox:
            with self.assertRaisesMessage(CommandError, 'resume_text'):
                call_command('import_job_alerts', config=path, profile_id=999, stdout=StringIO())
            mailbox.assert_not_called()

