"""Synthetic fixtures modeled on recognized Indeed links and unresolved peers."""
import json

from django.test import SimpleTestCase, TestCase

from applications.models import Application
from jobs.models import Job
from jobs.services.job_alerts import classify_alert, import_alert_messages, parse_alert
from jobs.tests.test_discovery import NOW
from jobs.tests.test_job_alerts import email_message, posting


def indeed_url(identity='abc123def456'):
    return 'https://ae.indeed.com/rc/clk/dl?jk=' + identity + '&tracking=PRIVATE'


def card(title='Python Developer', company='Example Employer', location='Dubai', identity='abc123def456'):
    return f'''<article class="job-card"><h3>{title}</h3>
    <span data-testid="company-name">{company}</span>
    <span data-testid="job-location">{location}</span>
    <div class="job-snippet">Develop Python Django services. 1-3 years experience.</div>
    <a href="{indeed_url(identity)}">View full job description</a></article>'''


class CardAssociationTests(SimpleTestCase):
    def test_unlabelled_description_paragraph_is_copied_only_from_explicit_listing(self):
        description = 'You will develop Python Django services and maintain reliable backend APIs.'
        html = card().replace('<div class="job-snippet">Develop Python Django services. 1-3 years experience.</div>',
                              '<p>' + description + '</p><p>Sent to private@example.com</p>')
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['description'], description)
        self.assertFalse(row['description_verified'])

    def test_text_description_is_not_the_entire_email_body(self):
        description = 'You will develop Python Django services and maintain reliable backend APIs.'
        text = ('Title: Python Developer\nCompany: Employer\nLocation: Dubai\n'
                + description + '\n' + indeed_url() + '\nSent to private@example.com')
        row = list(parse_alert(email_message('indeed', text=text), 'indeed'))[0]
        self.assertEqual(row['description'], description)
        self.assertNotIn('private@example.com', row['description'])

    def test_indeed_extracts_heading_semantic_metadata_and_available_snippet(self):
        rows = list(parse_alert(email_message('indeed', html=card()), 'indeed'))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['title'], 'Python Developer')
        self.assertEqual(row['title_evidence'], 'heading')
        self.assertEqual(row['company'], 'Example Employer')
        self.assertEqual(row['location'], 'Dubai')
        self.assertIn('Python Django', row['description'])
        self.assertEqual(row['url'], 'https://www.indeed.com/viewjob?jk=abc123def456')
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertIn('publication_date_unverified', data['review_reasons'])
        self.assertIn('description_completeness_unverified', data['review_reasons'])

    def test_multiple_cards_never_share_metadata_or_description(self):
        html = card() + card('Backend Engineer', 'Other Employer', 'Abu Dhabi', 'def456abc123')
        rows = list(parse_alert(email_message('indeed', html=html), 'indeed'))
        self.assertEqual({row['source_id']: (row['title'], row['company'], row['location']) for row in rows},
                         {'abc123def456': ('Python Developer', 'Example Employer', 'Dubai'),
                          'def456abc123': ('Backend Engineer', 'Other Employer', 'Abu Dhabi')})

    def test_non_target_heading_remains_rejected_even_with_target_email_header(self):
        html = '<h1>New Python jobs for you</h1>' + card('Accountant')
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], 'Accountant')
        self.assertEqual(classify_alert(row, 'python django', NOW)[1], 'rejected_role')

    def test_unknown_title_becomes_review_not_an_action_label_or_invented_role(self):
        html = ('<h1>New Python jobs for you</h1><article class="job-card">'
                '<span data-testid="company-name">Employer</span><span data-testid="location">Dubai</span>'
                f'<a href="{indeed_url()}">View full job description</a></article>')
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], '')
        data, outcome = classify_alert(row, 'python django', NOW)
        self.assertEqual(outcome, 'review')
        self.assertIn('missing_title', data['review_reasons'])

    def test_known_title_link_is_not_replaced_with_target_words_from_description(self):
        html = f'<article><a href="{indeed_url()}">Frontend Developer</a><p class="description">Python Django integrations</p></article>'
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], 'Frontend Developer')
        self.assertEqual(classify_alert(row, 'python django', NOW)[1], 'rejected_role')

    def test_opaque_title_link_inside_explicit_card_can_supply_its_own_heading(self):
        html = ('<article class="job-card"><h3><a href="https://alerts.indeed.com/opaque/PRIVATE">Python Developer</a></h3>'
                '<span class="company">Employer</span><span class="location">Dubai</span>'
                f'<a href="{indeed_url()}">Apply now</a></article>')
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], 'Python Developer')
        self.assertEqual(row['company'], 'Employer')

    def test_unresolved_neighbor_does_not_supply_metadata_to_recognized_link(self):
        html = (f'<table><tr><td><a href="{indeed_url()}">Python Developer</a></td></tr>'
                '<tr><td><h3><a href="https://alerts.indeed.com/opaque/PRIVATE">Backend Engineer</a></h3>'
                '<p>Company: Unrelated Employer</p><p>Location: Sharjah</p></td></tr></table>')
        row = list(parse_alert(email_message('indeed', html=html), 'indeed'))[0]
        self.assertEqual(row['title'], 'Python Developer')
        self.assertEqual(row['company'], '')
        self.assertEqual(row['location'], '')

    def test_naukri_recognized_direct_job_uses_same_local_metadata_rules(self):
        html = card().replace(indeed_url(), 'https://www.naukrigulf.com/python-jobs-in-dubai-jid-101126000123')
        row = list(parse_alert(email_message('naukrigulf', html=html), 'naukrigulf'))[0]
        self.assertEqual(row['source'], 'naukrigulf')
        self.assertEqual(row['company'], 'Example Employer')
        self.assertIn('Python Django', row['description'])


class CandidateCounterTests(TestCase):
    def test_all_rejection_counters_are_present_even_when_zero(self):
        counts = import_alert_messages([], 'private cv', dry_run=True, now=NOW)
        for name in ('rejected_role', 'rejected_location', 'rejected_date', 'rejected_experience', 'rejected_score'):
            self.assertIn(name, counts)
            self.assertEqual(counts[name], 0)

    def test_known_filter_failures_have_aggregate_and_source_counters(self):
        cases = [({'title': 'Accountant'}, 'python django', 'rejected_role'),
                 ({'datePosted': '2020-01-01T00:00:00Z'}, 'python django', 'rejected_date'),
                 ({'description': 'Python Django. 5 years experience'}, 'python django', 'rejected_experience'),
                 ({}, 'unrelated', 'rejected_score')]
        for changes, cv, expected in cases:
            with self.subTest(expected=expected):
                value = posting('indeed', **changes)
                html = '<script type="application/ld+json">' + json.dumps(value) + '</script>'
                counts = import_alert_messages([('indeed', email_message('indeed', html=html))], cv, dry_run=True, now=NOW)
                self.assertEqual(counts[expected], 1)
                self.assertEqual(counts['source.indeed.' + expected], 1)

    def test_review_candidates_are_readonly_and_counters_contain_no_private_values(self):
        counts = import_alert_messages([('indeed', email_message('indeed', html=card()))],
                                       'PRIVATE CV python django', now=NOW, dry_run=True)
        self.assertEqual(counts['would_create_review'], 1)
        self.assertEqual(counts['candidate_missing_company'], 0)
        self.assertEqual(counts['candidate_missing_location'], 0)
        self.assertEqual(counts['candidate_missing_description'], 0)
        self.assertEqual(counts['candidate_title_evidence_heading'], 1)
        self.assertNotIn('PRIVATE', repr(counts))
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())
