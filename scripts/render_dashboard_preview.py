"""Render synthetic UI previews using isolated in-memory SQLite, never .env."""
import os
import shutil
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.test_settings'

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from applications.models import Application
from applications.services.preparation import prepare_draft
from jobs.models import Job, DiscoveryRun, RejectedVacancy
from profiles.models import CandidateProfile

if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3' or settings.DATABASES['default']['NAME'] != ':memory:':
    raise SystemExit('Preview requires the isolated in-memory database')
settings.ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
call_command('migrate', verbosity=0, interactive=False)
user = get_user_model().objects.create_user('preview-reviewer', is_staff=True)
profile = CandidateProfile.objects.create(name='Demo Candidate', resume_text='Python Django PostgreSQL FastAPI React Docker')
now = timezone.now()
jobs = []
for index, (company, title, location, source) in enumerate([
    ('Crescent Systems', 'Python Django Developer', 'Dubai, UAE', 'company_greenhouse'),
    ('Harbour Technology', 'Backend Engineer', 'Abu Dhabi, UAE', 'company_ashby'),
    ('Atlas Digital', 'Full Stack Developer', 'Sharjah, UAE', 'linkedin'),
    ('Palm Software', 'Software Engineer', 'Remote - UAE', 'indeed'),
    ('Northstar AI', 'AI Developer', 'Dubai, UAE', 'company_ashby'),
    ('Meridian Labs', 'Senior Backend Engineer', 'Dubai, UAE', 'company_greenhouse'),
]):
    job = Job.objects.create(source=source, source_id=str(index), company=company, title=title,
        location=location, url=f'https://careers.example.com/jobs/{index}',
        posted_at=now - timedelta(days=1 if index < 4 else 30), min_experience=1, max_experience=3,
        description='Build Python Django and PostgreSQL services. Collaborate with product teams on reliable REST APIs. Required: 1-3 years experience.',
        requires_review=index == 2, review_reasons=['description_completeness_unverified'] if index == 2 else [],
        accepting_applications=index != 2)
    jobs.append(job)
Application.objects.create(candidate=profile, job=jobs[3], status='submitted', confirmation_reference='DEMO-CONFIRMATION', submitted_at=now)
draft = prepare_draft(jobs[0].pk, profile.pk)
# Synthetic history only: this preview never reads the live database or mailbox.
run = DiscoveryRun.objects.create(candidate=profile, status='completed', started_at=now,
    finished_at=now, totals={'fetched': 112, 'stage.role_pass': 24, 'stage.uae_pass': 13,
                            'stage.date_pass': 0, 'eligible': 0, 'review': 0, 'rejected': 112},
    source_names=['demo_employer_board'], source_counts={'demo_employer_board': {
        'fetched': 112, 'stage.role_pass': 24, 'stage.uae_pass': 13, 'rejected': 112}})
for job in jobs:
    RejectedVacancy.objects.create(run=run, company=job.company, title=job.title,
        location=job.location, source=job.source, posted_at=now - timedelta(days=30),
        rejection_code='rejected_date')
client = Client()
client.force_login(user)
destination = ROOT / 'output' / 'dashboard_preview'
destination.mkdir(parents=True, exist_ok=True)
for filename, url in [('index.html', '/'), ('job.html', f'/jobs/{jobs[0].pk}/'),
                      ('draft.html', f'/drafts/{draft.pk}/'), ('history.html', f'/discovery/runs/{run.pk}/')]:
    response = client.get(url)
    if response.status_code != 200:
        raise SystemExit('Preview rendering failed')
    (destination / filename).write_bytes(response.content)
static = destination / 'static' / 'applications'
static.mkdir(parents=True, exist_ok=True)
for filename in ('dashboard.css', 'dashboard.js'):
    shutil.copy2(ROOT / 'applications' / 'static' / 'applications' / filename, static / filename)
print('Rendered synthetic dashboard, vacancy and draft previews under output/dashboard_preview.')
