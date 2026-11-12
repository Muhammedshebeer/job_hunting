from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from applications.services.daily import run_daily_job_processing
from applications.services.budget import uae_day
from applications.tests.test_preparation import make_job, make_profile
from jobs.models import DiscoveryRun, RejectedVacancy
from jobs.services.retention import cleanup_rejected_vacancies
from jobs.tests.test_discovery import NOW


class RejectedVacancyRetentionTests(TestCase):
    def setUp(self):
        self.clock = patch('django.utils.timezone.now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.profile = make_profile()
        self.run = DiscoveryRun.objects.create(candidate=self.profile, status='completed', scheduled_for=uae_day())

    def row(self, created_at):
        row = RejectedVacancy.objects.create(run=self.run, source='test', rejection_code='rejected_date',
            posted_at=NOW - timedelta(days=100))
        RejectedVacancy.objects.filter(pk=row.pk).update(observed_at=created_at)
        return row

    def test_strict_creation_boundary_and_unrelated_records_are_preserved(self):
        cutoff = NOW - timedelta(days=5)
        old = self.row(cutoff - timedelta(microseconds=1))
        boundary, recent = self.row(cutoff), self.row(NOW)
        job = make_job()
        self.assertEqual(cleanup_rejected_vacancies(), 1)
        self.assertFalse(RejectedVacancy.objects.filter(pk=old.pk).exists())
        self.assertSetEqual(set(RejectedVacancy.objects.values_list('pk', flat=True)), {boundary.pk, recent.pk})
        self.assertTrue(DiscoveryRun.objects.filter(pk=self.run.pk).exists())
        self.assertTrue(type(job).objects.filter(pk=job.pk).exists())
        self.assertEqual(cleanup_rejected_vacancies(), 0)

    def test_command_dry_run_then_delete(self):
        self.row(NOW - timedelta(days=6))
        output = StringIO()
        call_command('cleanup_rejected_vacancies', dry_run=True, stdout=output)
        self.assertIn('Would delete 1', output.getvalue())
        self.assertEqual(RejectedVacancy.objects.count(), 1)
        output = StringIO()
        call_command('cleanup_rejected_vacancies', stdout=output)
        self.assertIn('Deleted 1', output.getvalue())
        self.assertFalse(RejectedVacancy.objects.exists())

    def test_daily_processing_cleans_even_when_todays_discovery_already_completed(self):
        self.row(NOW - timedelta(days=6))
        staff = get_user_model().objects.create_user(username='retention-staff', is_staff=True)
        with patch('applications.services.daily.process_next_run') as discovery:
            run_daily_job_processing(self.profile, staff)
        discovery.assert_not_called()
        self.assertFalse(RejectedVacancy.objects.exists())
