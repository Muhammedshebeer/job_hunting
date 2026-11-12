from contextvars import Context
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from applications.models import Application
from applications.tests.test_preparation import make_profile
from jobs.models import DiscoveryRun, Job, RejectedVacancy
from jobs.services.discovery import DiscoveryError
from jobs.services.discovery_adapters import run_plan
from jobs.services.discovery_adapters import ADAPTERS
from jobs.services.discovery_lock import (
    DiscoveryBusy, DiscoveryOwnershipLost, discovery_execution_lock,
)
from jobs.services.discovery_runs import enqueue_discovery, process_next_run
from jobs.services.discovery_pipeline import ingest_rows
from jobs.tests.test_discovery import NOW
from jobs.tests.test_phase2c import api_source, full_row


class DiscoveryQueueTests(TestCase):
    def setUp(self):
        self.profile = make_profile(resume_text='Python Django')
        self.staff = get_user_model().objects.create_user(username='worker-test', is_staff=True)

    def queue(self):
        return enqueue_discovery(self.profile, self.staff)[0]

    def execute(self, rows=None, error=None):
        def pipeline(sources, cv, **kwargs):
            if error:
                raise error
            return run_plan(sources, cv, now=NOW, fetcher=lambda _: {'jobs': rows or []}, **kwargs)
        with patch('jobs.services.discovery_runs.load_plan', return_value=[api_source()]), patch(
                'jobs.services.discovery_runs.run_plan', side_effect=pipeline):
            return process_next_run()

    def test_global_queue_is_idempotent_across_profiles_and_does_not_fetch(self):
        with patch('jobs.services.discovery_runs.run_plan') as pipeline:
            first, created = enqueue_discovery(self.profile, self.staff)
            second, again = enqueue_discovery(make_profile(), self.staff)
        self.assertTrue(created)
        self.assertFalse(again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, 'queued')
        pipeline.assert_not_called()
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_database_constraint_prevents_a_second_active_run(self):
        run = self.queue()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DiscoveryRun.objects.create(candidate=self.profile, status='running')
        run.status = 'completed'
        run.save()
        self.assertNotEqual(self.queue().pk, run.pk)

    def test_queue_requires_staff_and_existing_candidate_evidence(self):
        self.staff.is_staff = False
        with self.assertRaises(ValidationError):
            self.queue()
        self.staff.is_staff = True
        self.profile.resume_text = ''
        with self.assertRaises(ValidationError):
            self.queue()
        self.assertFalse(DiscoveryRun.objects.exists())

    def test_worker_keeps_rejections_separate_and_saves_only_eligible_and_review_jobs(self):
        self.queue()
        rows = [full_row(), full_row(source_id='old', url='https://careers.example.com/jobs/old',
                                    posted_at=(NOW - timedelta(days=11)).isoformat()),
                full_row(source_id='review', url='https://careers.example.com/jobs/review', posted_at=None)]
        run = self.execute(rows)
        self.assertEqual(run.status, 'completed')
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(run.totals['fetched'], 3)
        self.assertEqual(run.totals['policy.minimum_cv_score'], 25)
        self.assertEqual(run.totals['policy.publication_window_days'], 3)
        self.assertEqual(run.totals['eligible'], 1)
        self.assertEqual(run.totals['review'], 1)
        self.assertEqual(run.totals['rejected'], 1)
        self.assertEqual(run.source_counts['employer_api']['stage.date_pass'], 1)
        self.assertEqual(Job.objects.count(), 2)
        self.assertTrue(Job.objects.get(source_id='review').requires_review)
        audit = RejectedVacancy.objects.get()
        self.assertEqual(audit.rejection_code, 'rejected_date')
        self.assertEqual(audit.company, 'Example')
        self.assertEqual(audit.posted_at, NOW - timedelta(days=11))
        fields = {field.name for field in audit._meta.fields}
        self.assertFalse(fields & {'job', 'application', 'url', 'description', 'resume_text'})
        self.assertFalse(Application.objects.exists())

    def test_worker_uses_all_strict_rejection_rules(self):
        self.queue()
        changes = [{'title': 'Accountant'},
                   {'posted_at': (NOW - timedelta(days=12)).isoformat()},
                   {'min_experience': 5, 'max_experience': 8}, {'description': 'Unrelated work'},
                   {'accepting_applications': False}]
        rows = [full_row(source_id=str(i), url=f'https://careers.example.com/jobs/{i}', **change)
                for i, change in enumerate(changes)]
        self.assertEqual(self.execute(rows).status, 'completed')
        self.assertEqual(set(RejectedVacancy.objects.values_list('rejection_code', flat=True)), {
            'rejected_role', 'rejected_date', 'rejected_experience',
            'rejected_score', 'rejected_closed'})
        self.assertFalse(Job.objects.exists())
        self.assertFalse(Application.objects.exists())

    def test_private_failure_is_sanitized_and_queue_can_retry(self):
        self.queue()
        run = self.execute(error=RuntimeError('PRIVATE password token resume email body'))
        self.assertEqual(run.status, 'failed')
        self.assertNotIn('PRIVATE', run.error_summary)
        self.assertIsNotNone(run.finished_at)
        self.assertNotEqual(self.queue().pk, run.pk)

    def test_expected_source_failure_is_reported_without_private_error(self):
        self.queue()
        def pipeline(sources, cv, **kwargs):
            def fetch(_):
                raise DiscoveryError('PRIVATE source credentials')
            return run_plan(sources, cv, now=NOW, fetcher=fetch, **kwargs)
        with patch('jobs.services.discovery_runs.load_plan', return_value=[api_source()]), patch(
                'jobs.services.discovery_runs.run_plan', side_effect=pipeline):
            run = process_next_run()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.source_counts['employer_api']['failed_sources'], 1)
        self.assertNotIn('PRIVATE', run.error_summary)

    def test_abandoned_running_row_is_failed_only_after_lock_acquisition(self):
        run = self.queue()
        run.status = 'running'
        run.save()
        with discovery_execution_lock():
            with self.assertRaises(DiscoveryBusy):
                Context().run(process_next_run)
            run.refresh_from_db()
            self.assertEqual(run.status, 'running')
        self.assertIsNone(process_next_run())
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')

    def test_partial_source_failure_preserves_rejected_snapshots_and_counts(self):
        self.queue()
        def rows(source, context):
            yield full_row(title='Accountant')
            raise DiscoveryError('PRIVATE remote response')
        with patch.dict(ADAPTERS, authorized_json=(rows, lambda source: None)):
            run = self.execute()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(run.totals['fetched'], 1)
        self.assertEqual(run.totals['failed_sources'], 1)
        self.assertEqual(run.rejected_vacancies.count(), 1)

    def test_audit_redacts_contact_details_and_does_not_retain_raw_payload(self):
        self.queue()
        run = self.execute([full_row(title='Accountant private@example.test https://example.test/private',
                                   company='Synthetic private@example.test')])
        audit = run.rejected_vacancies.get()
        self.assertNotIn('private@example.test', audit.company)
        self.assertNotIn('https://', audit.title)

    def test_dry_run_has_no_persistent_history_observers_or_database_writes(self):
        with CaptureQueriesContext(connection) as queries:
            run_plan([api_source()], 'python django', dry_run=True, now=NOW,
                     fetcher=lambda _: {'jobs': [full_row(title='Accountant')]})
        mutations = [q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertEqual(mutations, [])
        self.assertFalse(DiscoveryRun.objects.exists())
        self.assertFalse(RejectedVacancy.objects.exists())
        with self.assertRaises(DiscoveryError):
            run_plan([], '', dry_run=True, on_rejected=lambda *args: None)
        with self.assertRaises(DiscoveryError):
            ingest_rows([], 'test', '', method='authorized_json', dry_run=True,
                        on_rejected=lambda *args: None)

    def test_production_worker_refuses_sqlite(self):
        with self.assertRaises(CommandError):
            call_command('discovery_worker', once=True, stdout=StringIO())


class DiscoveryLockTests(SimpleTestCase):
    def test_nested_lock_reuses_ownership_but_other_context_is_blocked(self):
        with discovery_execution_lock() as owner:
            with discovery_execution_lock() as nested:
                self.assertIs(owner, nested)
            with self.assertRaises(DiscoveryBusy):
                Context().run(lambda: discovery_execution_lock().__enter__())
        with discovery_execution_lock():
            pass

    def test_postgres_lock_uses_one_session_and_unlocks(self):
        fake = MagicMock(vendor='postgresql')
        fake.connection.closed = False
        cursor = fake.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        with patch('jobs.services.discovery_lock.connection', fake):
            with discovery_execution_lock() as owner:
                owner.check()
        self.assertIn('pg_try_advisory_lock', cursor.execute.call_args_list[0].args[0])
        self.assertIn('pg_advisory_unlock', cursor.execute.call_args_list[-1].args[0])

    def test_lost_postgres_session_stops_without_unlocking_replacement(self):
        fake = MagicMock(vendor='postgresql')
        fake.connection.closed = False
        cursor = fake.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        with patch('jobs.services.discovery_lock.connection', fake):
            with discovery_execution_lock() as owner:
                fake.connection = MagicMock(closed=False)
                with self.assertRaises(DiscoveryOwnershipLost):
                    owner.check()
        self.assertEqual(cursor.execute.call_count, 1)
