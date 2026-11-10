import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch
from xml.etree import ElementTree as ET

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from applications.models import Application, DailyReport
from applications.services.daily import run_daily_job_processing
from applications.services.reports import queue_report, report_context
from applications.services.scheduled import preview_job_report, send_scheduled_report
from applications.services.task_schedule import task_definitions, NAMESPACE
from applications.tests.test_preparation import make_profile, make_job
from applications.tests.test_daily_automation import CONFIG
from jobs.models import DiscoveryRun
from jobs.services.discovery_adapters import run_plan
from jobs.services.discovery_lock import DiscoveryBusy, discovery_execution_lock
from jobs.tests.test_discovery import NOW
from jobs.tests.test_phase2c import api_source, full_row


@override_settings(REPORT_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SplitScheduleTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile = make_profile()
        self.staff = get_user_model().objects.create_user(username='split-staff', is_staff=True)
        mail.outbox = []

    def test_0730_task_prepares_drafts_but_does_not_even_queue_a_report(self):
        def pipeline(sources, cv, **kwargs):
            return run_plan(sources, cv, now=NOW,
                fetcher=lambda _: {'jobs': [full_row(url='https://careers.fixture-employer.com/jobs/new')]}, **kwargs)
        with patch('jobs.services.discovery_runs.load_plan', return_value=[api_source()]), patch(
                'jobs.services.discovery_runs.run_plan', side_effect=pipeline) as discovery, patch(
                'applications.services.submission.AuthorizedAdapter.submit') as submit:
            first, counts = run_daily_job_processing(self.profile, self.staff)
            again, repeated = run_daily_job_processing(self.profile, self.staff)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.status, 'completed')
        self.assertEqual(counts['prepared'], 1)
        self.assertEqual(repeated['prepared'], 0)
        self.assertEqual(discovery.call_count, 1)
        self.assertFalse(DailyReport.objects.exists())
        self.assertFalse(mail.outbox)
        self.assertEqual(Application.objects.get().status, 'review_required')
        submit.assert_not_called()

    def test_agent_dry_run_is_database_read_only_and_cannot_send_email(self):
        def preview(profile):
            return run_plan([api_source()], profile.resume_text, dry_run=True, now=NOW,
                fetcher=lambda _: {'jobs': [full_row()]})
        output = StringIO()
        with patch('applications.management.commands.run_daily_job_agent.preview_job_agent', side_effect=preview), CaptureQueriesContext(connection) as queries:
            call_command('run_daily_job_agent', profile_id=self.profile.pk, staff_id=self.staff.pk,
                dry_run=True, stdout=output)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.assertFalse(Application.objects.exists())
        self.assertFalse(DiscoveryRun.objects.exists())
        self.assertFalse(DailyReport.objects.exists())
        self.assertIn('eligible: 1', output.getvalue())
        self.assertFalse(mail.outbox)

    def test_report_dry_run_overrides_test_email_and_never_looks_up_smtp_credentials(self):
        make_job()
        with patch('applications.services.reports.load_delivery_config') as config, CaptureQueriesContext(connection) as queries:
            call_command('send_daily_job_report', profile_id=self.profile.pk, dry_run=True, test_email=True, stdout=StringIO())
        config.assert_not_called()
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.assertFalse(DailyReport.objects.exists())
        self.assertFalse(mail.outbox)

    def test_busy_discovery_defers_reporting_for_exact_bound_without_queuing(self):
        with patch('applications.services.scheduled.discovery_execution_lock', side_effect=DiscoveryBusy), patch(
                'applications.services.scheduled.time.sleep') as sleep:
            with self.assertRaises(DiscoveryBusy):
                send_scheduled_report(self.profile, retries=2, retry_seconds=5)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(DailyReport.objects.exists())
        self.assertFalse(mail.outbox)

    def test_report_succeeds_after_busy_lock_clears_and_remains_deduplicated(self):
        attempts = []
        @contextmanager
        def busy_once():
            attempts.append(1)
            if len(attempts) == 1:
                raise DiscoveryBusy()
            with discovery_execution_lock() as owner:
                yield owner
        with patch('applications.services.scheduled.discovery_execution_lock', busy_once), patch(
                'applications.services.scheduled.time.sleep'), patch(
                'applications.services.reports.load_delivery_config', return_value=CONFIG):
            report = send_scheduled_report(self.profile, retries=1)
            same = send_scheduled_report(self.profile)
        self.assertEqual(report.pk, same.pk)
        self.assertEqual(report.status, 'sent')
        self.assertEqual(len(mail.outbox), 1)

    def test_report_snapshot_prefers_completed_run_and_labels_old_snapshot(self):
        complete = DiscoveryRun.objects.create(candidate=self.profile, status='completed',
            finished_at=NOW - timedelta(days=1), scheduled_for=(NOW - timedelta(days=1)).date(), totals={'fetched': 19})
        DiscoveryRun.objects.create(candidate=self.profile, status='running', totals={'fetched': 999})
        counts, run, note = preview_job_report(self.profile)
        self.assertEqual(run.pk, complete.pk)
        self.assertIn('earlier or unscheduled', note)
        self.assertIn('partial counts are not included', note)
        report, _ = queue_report(self.profile)
        self.assertEqual(report_context(report)['summary']['fetched'], 19)

    def test_failed_only_snapshot_is_never_presented_as_completed(self):
        DiscoveryRun.objects.create(candidate=self.profile, status='failed', finished_at=NOW)
        _, run, note = preview_job_report(self.profile)
        self.assertEqual(run.status, 'failed')
        self.assertIn('failed/partial', note)

    def test_pending_approval_is_separate_from_manual_ready_queue(self):
        make_job()
        report, _ = queue_report(self.profile)
        sections = dict(report_context(report)['sections'])
        self.assertEqual(len(sections['Pending approval']), 1)
        self.assertEqual(sections['Manual applications required'], [])

    def test_scheduled_commands_refuse_unlocked_non_postgres_execution(self):
        for name, kwargs in (('run_daily_job_agent', {'staff_id': self.staff.pk}), ('send_daily_job_report', {})):
            with self.assertRaises(CommandError):
                call_command(name, profile_id=self.profile.pk, **kwargs, stdout=StringIO())

    def test_invalid_ids_and_retry_bounds_fail_before_writes(self):
        with self.assertRaises(CommandError):
            call_command('run_daily_job_agent', profile_id=self.profile.pk, staff_id=999, dry_run=True, stdout=StringIO())
        for retries, seconds in ((-1, 30), (11, 30), (6, 0), (6, 61)):
            with self.assertRaises(CommandError):
                call_command('send_daily_job_report', profile_id=self.profile.pk,
                    lock_retries=retries, retry_seconds=seconds, dry_run=True, stdout=StringIO())
        self.assertFalse(DailyReport.objects.exists())

    def test_cli_busy_failure_is_sanitized_and_nonzero_command_error(self):
        with patch('applications.management.commands.send_daily_job_report.connection') as database, patch(
                'applications.management.commands.send_daily_job_report.send_scheduled_report', side_effect=DiscoveryBusy('PRIVATE')):
            database.vendor = 'postgresql'
            with self.assertRaises(CommandError) as failure:
                call_command('send_daily_job_report', profile_id=self.profile.pk, stdout=StringIO())
        self.assertIn('bounded retries', str(failure.exception))
        self.assertNotIn('PRIVATE', str(failure.exception))

    def test_test_email_flag_is_real_test_delivery_without_daily_reservation(self):
        with patch('applications.management.commands.send_daily_job_report.connection') as database, patch(
                'applications.services.reports.load_delivery_config', return_value=CONFIG):
            database.vendor = 'postgresql'
            call_command('send_daily_job_report', profile_id=self.profile.pk, test_email=True, stdout=StringIO())
        self.assertEqual(DailyReport.objects.get().kind, 'test')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('TEST', mail.outbox[0].subject)
        self.assertFalse(Application.objects.exists())

    def test_report_cli_smtp_failure_is_nonzero_and_never_logs_private_exception(self):
        output = StringIO()
        with patch('applications.management.commands.send_daily_job_report.connection') as database, patch(
                'applications.services.reports.load_delivery_config', return_value=CONFIG), patch(
                'django.core.mail.message.EmailMessage.send', side_effect=OSError('PRIVATE SMTP password')):
            database.vendor = 'postgresql'
            with self.assertRaises(CommandError) as failure:
                call_command('send_daily_job_report', profile_id=self.profile.pk, stdout=output)
        self.assertEqual(DailyReport.objects.get().status, 'delivery_unknown')
        self.assertNotIn('PRIVATE', output.getvalue() + str(failure.exception))
        self.assertFalse(mail.outbox)

    def test_newer_failed_attempt_is_explicit_when_using_older_completed_snapshot(self):
        complete = DiscoveryRun.objects.create(candidate=self.profile, status='completed', finished_at=NOW - timedelta(days=1))
        failed = DiscoveryRun.objects.create(candidate=self.profile, status='failed', finished_at=NOW)
        _, run, note = preview_job_report(self.profile)
        self.assertEqual(run.pk, complete.pk)
        self.assertIn(f'#{failed.pk} failed', note)

    def test_sent_report_persists_selected_completed_run_for_audit(self):
        complete = DiscoveryRun.objects.create(candidate=self.profile, status='completed', finished_at=NOW)
        with patch('applications.services.reports.load_delivery_config', return_value=CONFIG):
            report = send_scheduled_report(self.profile)
        report.refresh_from_db()
        self.assertEqual(report.discovery_run_id, complete.pk)
        self.assertEqual(report.status, 'sent')


class TaskDefinitionTests(SimpleTestCase):
    def test_two_dubai_triggers_use_explicit_offset_hidden_shell_and_exit_codes(self):
        definitions = task_definitions('project', 1, 2, 'S-1-5-21-123', now=NOW)
        self.assertEqual(len(definitions), 2)
        ns = {'t': NAMESPACE}
        for name, xml in definitions.items():
            root = ET.fromstring(xml)
            boundary = root.find('t:Triggers/t:CalendarTrigger/t:StartBoundary', ns).text
            self.assertTrue(boundary.endswith('+04:00'))
            self.assertIn('T07:30:00' if '0730' in name else 'T08:00:00', boundary)
            self.assertEqual(root.find('t:Settings/t:MultipleInstancesPolicy', ns).text, 'IgnoreNew')
            args = root.find('t:Actions/t:Exec/t:Arguments', ns).text
            self.assertIn('-WindowStyle Hidden', args)
            script = base64.b64decode(args.split()[-1]).decode('utf-16le')
            self.assertIn('exit $LASTEXITCODE', script)
            self.assertIn('exit 1', script)
            self.assertNotIn('--test-email', script)
            self.assertNotIn('password', script.lower())

    def test_boundary_rolls_to_next_occurrence_in_dubai_even_on_utc_host(self):
        ns = {'t': NAMESPACE}
        before = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)  # 07:00 Dubai
        after = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)   # 09:00 Dubai
        for now, expected in ((before, '2026-11-10'), (after, '2026-11-11')):
            for xml in task_definitions('project', 1, 2, 'S-1-5-21-123', now=now).values():
                self.assertTrue(ET.fromstring(xml).find('t:Triggers/t:CalendarTrigger/t:StartBoundary', ns).text.startswith(expected))

    def test_xml_escapes_project_path_and_rejects_invalid_parameters(self):
        definitions = task_definitions("project & O'Neil", 1, 2, 'S-1-5-21-123', now=NOW)
        for xml in definitions.values():
            root = ET.fromstring(xml)
            args = root.find('{*}Actions/{*}Exec/{*}Arguments').text
            self.assertIn("O''Neil", base64.b64decode(args.split()[-1]).decode('utf-16le'))
        for profile, staff, sid in ((0, 1, 'S-1-5'), (1, -1, 'S-1-5'), (1, 1, 'not a SID')):
            with self.assertRaises(ValueError):
                task_definitions('project', profile, staff, sid)
