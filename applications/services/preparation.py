"""Grounded local drafts and explicit human review. No network/submission calls."""
from jobs import qualification as policy
import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit
from ipaddress import ip_address

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from applications.models import Application, ApplicationEvent
from jobs.models import Job
from jobs.services.discovery import ROLE_PATTERN, experience
from jobs.services.discovery import DiscoveryError, canonical_url
from jobs.services.matching import score_job, SKILLS, ALIASES
from jobs.services.locations import location_group
from profiles.models import CandidateProfile


DEFAULT_QUESTIONS = {
    'notice_period': 'What is your notice period?',
    'joining_availability': 'When are you available to join?',
    'immediate_availability': 'Are you available immediately?',
    'visa_status': 'What is your current UAE visa status?',
    'sponsorship': 'Will you need employer sponsorship or a work-permit transfer?',
    'work_authorization': 'Are you authorized to work for this new employer without restrictions?',
}


def safe_destination(url):
    try:
        return canonical_url(url)
    except (DiscoveryError, ValueError, TypeError):
        return ''


def same_vacancy(left, right):
    from jobs.services.discovery_pipeline import employer_identity, normalized_identity
    try:
        if normalized_identity(left.url) == normalized_identity(right.url):
            return True
    except (DiscoveryError, ValueError, TypeError):
        pass
    first = employer_identity(left.discovery_metadata)
    second = employer_identity(right.discovery_metadata)
    return bool(first and first == second)


def profile_issues(profile):
    issues = []
    if not profile.active:
        issues.append('Candidate profile is inactive.')
    if not profile.name.strip():
        issues.append('Candidate name is missing.')
    if not profile.resume_text.strip():
        issues.append('Extracted CV text is missing.')
    if profile.notice_days != 60 or not profile.employment_visa or not profile.needs_transfer:
        issues.append('Profile conflicts with the verified 60-day UAE employment-visa and transfer policy. Clarify the profile first.')
    return issues


def synthetic_vacancy(job):
    metadata = job.discovery_metadata if isinstance(job.discovery_metadata, dict) else {}
    host = (urlsplit(safe_destination(job.url)).hostname or '').lower()
    reserved = ('example.com', 'example.net', 'example.org', 'example', 'test', 'invalid', 'localhost', 'local', 'internal', 'lan')
    return (any(host == suffix or host.endswith('.' + suffix) for suffix in reserved)
            or any(metadata.get(key) is True for key in ('synthetic', 'is_synthetic', 'demo', 'test_fixture'))
            or job.source.lower().startswith(('synthetic', 'demo', 'test_')))


def public_application_url(job):
    url = safe_destination(job.url)
    if not url or synthetic_vacancy(job):
        return ''
    try:
        ip_address(urlsplit(url).hostname)
        return ''
    except ValueError:
        return url


def application_source_issues(job):
    """No network access: require the existing discovery provenance, not URL syntax alone."""
    metadata = job.discovery_metadata if isinstance(job.discovery_metadata, dict) else {}
    host = (urlsplit(safe_destination(job.url)).hostname or '').lower()
    try:
        ip_address(host)
        return ['An employer application destination must use a verified public hostname.']
    except ValueError:
        pass
    if synthetic_vacancy(job):
        return ['Synthetic or demonstration vacancies cannot enter the application queue.']
    method = metadata.get('method')
    if method not in ('greenhouse', 'ashby', 'authorized_json', 'mapped_json', 'csv', 'manual_link', 'manual_links', 'email_alert'):
        return ['Source provenance is unverified. Reimport through an authorized discovery source.']
    if not metadata.get('date_evidence') and metadata.get('date_verified_at_source') is not True:
        return ['Source publication evidence is unverified.']
    if metadata.get('publication_date_ignored_reason'):
        return ['Source publication semantics require review.']
    if metadata.get('description_evidence') != 'email JobPosting':
        return ['Complete job-description evidence is unverified.']
    return []


def cv_file_digest(profile):
    """Hash the existing upload privately; never extract, log or return CV contents."""
    if not profile.resume.name:
        return ''
    try:
        digest = hashlib.sha256()
        with profile.resume.storage.open(profile.resume.name, 'rb') as uploaded:
            for chunk in iter(lambda: uploaded.read(65536), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return ''


def job_assessment(job, profile, now=None):
    now = now or timezone.now()
    rejected, missing = [], []
    score, details = score_job(job.description, profile.resume_text)
    if not safe_destination(job.url):
        missing.append('A verified HTTPS application URL is missing.')
    if not job.title:
        missing.append('Job title is missing.')
    elif not ROLE_PATTERN.search(job.title):
        rejected.append('Role is outside the approved developer roles.')
    if not job.company:
        missing.append('Company is missing.')
    if not job.location:
        missing.append('Location is missing.')
    if job.posted_at is None:
        missing.append('Publication date is unverified.')
    elif timezone.is_naive(job.posted_at):
        missing.append('Publication timezone is unverified.')
    elif job.posted_at > now:
        rejected.append('Publication date is in the future.')
    elif job.posted_at < now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS):
        rejected.append(f'Publication is older than {policy.PUBLICATION_WINDOW_DAYS} days.')
    if not job.description:
        missing.append('Job description is missing.')
    bounds = experience({'description': job.description, 'min_experience': job.min_experience,
                         'max_experience': job.max_experience})
    if bounds is None or bounds[0] is None:
        missing.append('Experience requirements are unverified.')
    elif bounds[0] > 3 or bounds[1] is not None and bounds[1] < 1:
        rejected.append('Experience requirements do not overlap 1–3 years.')
    if job.requires_review:
        missing.append('Discovery evidence still requires review.')
        missing.extend(str(reason).replace('_', ' ').capitalize() for reason in job.review_reasons if isinstance(reason, str))
    if not job.accepting_applications:
        missing.append('Open application status is not confirmed.')
    from jobs.services.job_alerts import parse_publication
    metadata = job.discovery_metadata if isinstance(job.discovery_metadata, dict) else {}
    valid_through = parse_publication(metadata.get('valid_through'))
    if valid_through and valid_through < now:
        rejected.append('The source reports this vacancy expired.')
    if job.description and score < policy.MIN_CV_SCORE:
        # An incomplete excerpt is review evidence, not a reliable full-description score.
        (missing if job.requires_review else rejected).append(f'CV relevance is below {policy.MIN_CV_SCORE}%.')
    missing.extend(profile_issues(profile))
    missing.extend(application_source_issues(job))
    status = 'rejected' if rejected else 'needs_review' if missing else 'eligible'
    return {'status': status, 'score': score, 'matched': details.get('matched', []),
            'required': details.get('required', []), 'reasons': list(dict.fromkeys(rejected + missing))}


def prepare_answer(question, profile):
    text = question.lower()
    review, reason = False, ''
    if profile_issues(profile):
        return {'question': question, 'answer': '', 'requires_review': True, 'reason': 'Clarify verified candidate facts first.'}
    if re.search(r'passport|(?:visa|permit).*(?:number|expir|valid until)|(?:sponsor|transfer).*(?:process|procedure|cost|fee)', text):
        return {'question': question, 'answer': '', 'requires_review': True,
                'reason': 'These visa, permit, or transfer details have not been verified.'}
    if re.search(r'authori[sz]|unrestricted|right to work|legally|citizen|nationality|permanent residen|'
                 r'(?:already|valid|hold|possess).*work.?permit|work.?permit.*(?:this|new) (?:employer|company)', text):
        answer = 'Current UAE employment visa is under my current employer. New employer sponsorship or a work-permit transfer is generally required. Unrestricted authorization for this employer is not confirmed.'
        review, reason = True, 'A current employment visa does not establish unrestricted authorization for a different employer.'
    elif re.search(r'notice', text):
        answer = '60 days.'
    elif re.search(r'immediate|right away', text):
        answer = 'No. I have a 60-day notice period.'
    elif re.search(r'sponsor|transfer', text) or re.search(r'(?:need|require).*work.?permit', text):
        answer = 'Yes. New employer sponsorship or a work-permit transfer is generally required; I currently hold an employment visa sponsored by my current employer.'
    elif re.search(r'visa', text):
        answer = 'Current UAE employment visa under my current employer sponsorship.'
    elif re.search(r'join|start|available|availability', text):
        answer = 'After a 60-day notice period, subject to employer agreement and sponsorship/work-permit transfer. The exact joining date is not confirmed.'
        if re.search(r'\bdate\b|calendar|dd/mm|yyyy', text):
            review, reason = True, 'An exact joining date has not been verified; do not invent one.'
    else:
        answer, review, reason = '', True, 'No verified fact answers this question. Human clarification is required.'
    return {'question': question, 'answer': answer, 'requires_review': review, 'reason': reason}


def generate_cover_letter(job, profile):
    if profile_issues(profile):
        return ''
    _, details = score_job(job.description, profile.resume_text)
    matched = details.get('matched', [])
    opening = f'I am applying for the {job.title} role at {job.company}.' if job.title and job.company else 'Please consider my CV for this vacancy once the role and employer have been verified.'
    alignment = ('My CV references ' + ', '.join(matched) + ', which also appear in the supplied role description.'
                 if matched else 'Please review my CV against the supplied role description; no specific skill overlap has been verified.')
    return '\n\n'.join([
        'Dear Hiring Team,', opening, alignment,
        'I currently hold a UAE employment visa under my current employer sponsorship. I have a 60-day notice period and am not available immediately. New employer sponsorship or a work-permit transfer is generally required. My exact joining date must be agreed and is not yet confirmed.',
        'I would welcome the opportunity to discuss the role and the verified experience in my CV.',
        'Kind regards,\n' + profile.name,
    ])


def evidence_digest(job, profile, questions):
    payload = {
        'qualification_policy': policy.snapshot(),
        'scoring_policy': [SKILLS, ALIASES],
        'uploaded_cv_digest': cv_file_digest(profile),
        'profile': [profile.pk, profile.name, profile.active, profile.notice_days, profile.employment_visa,
                    profile.needs_transfer, profile.resume.name, hashlib.sha256(profile.resume_text.encode()).hexdigest()],
        'job': [job.pk, job.source, job.source_id, job.company, job.title, job.location, job.url, str(job.posted_at), job.description,
                job.min_experience, job.max_experience, job.requires_review, job.review_reasons,
                job.accepting_applications, job.discovery_metadata],
        'questions': questions,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def draft_digest(app):
    data = [app.application_url, app.company_snapshot, app.role_snapshot, app.cover_letter,
            app.draft_answers, app.preparation_snapshot]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def approval_blockers(app):
    assessment = job_assessment(app.job, app.candidate)
    blockers = list(assessment['reasons'])
    if not cv_file_digest(app.candidate):
        blockers.append('The existing uploaded CV is missing or unreadable.')
    snapshot = app.preparation_snapshot
    if not app.prepared_at or not snapshot or not app.cover_letter:
        blockers.append('Generate and review the draft first.')
    elif snapshot.get('evidence_digest') != evidence_digest(app.job, app.candidate, snapshot.get('questions', {})):
        blockers.append('Job or profile evidence changed. Regenerate the draft before approval.')
    if app.prepared_at and (app.application_url != safe_destination(app.job.url)
            or app.company_snapshot != app.job.company or app.role_snapshot != app.job.title):
        blockers.append('The draft vacancy snapshot differs from its source evidence. Regenerate it first.')
    for key, answer in app.draft_answers.items():
        if answer.get('requires_review') and not (answer.get('human_resolution') and answer.get('human_verified') is True):
            blockers.append('Clarification required: ' + answer.get('question', key))
    if app.status in ('approved', 'externally_opened') and app.approval_digest != draft_digest(app):
        blockers.append('Draft content changed after approval; approval is no longer valid.')
    return list(dict.fromkeys(blockers))


@transaction.atomic
def prepare_draft(job_id, profile_id, *, extra_questions='', user=None, only_if_missing=False):
    # Lock profile + job consistently for creation, regeneration, and approval.
    profile = CandidateProfile.objects.select_for_update().get(pk=profile_id, active=True)
    job = Job.objects.select_for_update().get(pk=job_id)
    for existing in Application.objects.filter(candidate=profile).exclude(job=job).select_related('job'):
        if same_vacancy(job, existing.job):
            # All preparation paths lock this profile first, serializing duplicate checks.
            return existing
    app, created = Application.objects.get_or_create(job=job, candidate=profile)
    if not created:
        app = Application.objects.select_for_update().get(pk=app.pk)
        if only_if_missing:
            app._newly_prepared = False
            return app
    if app.status in ('submitted', 'failed', 'skipped'):
        raise ValidationError('This application is terminal. Its existing record is preserved.')
    questions = dict(DEFAULT_QUESTIONS)
    for index, question in enumerate(extra_questions.splitlines()):
        if question.strip():
            questions[f'custom_{index}'] = question.strip()[:500]
    if len(questions) > 26:
        raise ValidationError('At most 20 additional questions are supported.')
    app.application_url, app.company_snapshot, app.role_snapshot = safe_destination(job.url), job.company, job.title
    app.cover_letter = generate_cover_letter(job, profile)
    app.draft_answers = {key: prepare_answer(question, profile) for key, question in questions.items()}
    if location_group(job.location) == 'other':
        for key in ('sponsorship', 'work_authorization'):
            app.draft_answers[key].update(answer='', requires_review=True,
                reason=f'Work authorization and sponsorship for {job.location} have not been verified. A UAE visa does not establish authorization for this location.')
    app.prepared_at = timezone.now()
    assessment = job_assessment(job, profile)
    app.preparation_snapshot = {'evidence_digest': evidence_digest(job, profile, questions), 'questions': questions,
                                'qualification_policy': policy.snapshot(),
                                'score': assessment['score'], 'matched': assessment['matched'],
                                'employment_policy': 'UAE employment visa; current employer sponsor; 60-day notice; employer transfer generally needed; exact joining date unconfirmed'}
    app.approved_at, app.approved_by, app.approval_digest = None, None, ''
    app.status = 'review_required' if approval_blockers(app) else 'ready_for_review'
    app.save()
    ApplicationEvent.objects.create(application=app, actor=user, action='draft_prepared' if created else 'draft_regenerated')
    app._newly_prepared = True
    return app


@transaction.atomic
def review_draft(application_id, user, *, resolutions, approve=False, skip=False, confirmed=False, expected_digest=''):
    original = Application.objects.get(pk=application_id)
    profile = CandidateProfile.objects.select_for_update().get(pk=original.candidate_id)
    job = Job.objects.select_for_update().get(pk=original.job_id)
    app = Application.objects.select_for_update().get(pk=application_id)
    app.candidate, app.job = profile, job
    if not user.is_active or not user.is_staff:
        raise ValidationError('An active staff reviewer is required.')
    if app.status in ('submitted', 'failed', 'skipped'):
        raise ValidationError('This application is terminal; its evidence is preserved.')
    if expected_digest != draft_digest(app):
        raise ValidationError('The draft changed in another window. Reload before reviewing.')
    if skip:
        app.status, app.approved_at, app.approved_by, app.approval_digest = 'skipped', None, None, ''
        app.save()
        ApplicationEvent.objects.create(application=app, actor=user, action='skipped')
        return app
    if app.preparation_snapshot.get('evidence_digest') != evidence_digest(job, profile, app.preparation_snapshot.get('questions', {})):
        raise ValidationError('Job or profile evidence changed. Regenerate the draft first.')
    for key, resolution in resolutions.items():
        if key in app.draft_answers and app.draft_answers[key].get('requires_review'):
            text = resolution.get('answer', '').strip() if isinstance(resolution, dict) else ''
            verified = isinstance(resolution, dict) and resolution.get('verified') is True
            app.draft_answers[key]['human_resolution'] = text
            app.draft_answers[key]['human_verified'] = verified and bool(text)
            if text and verified:
                app.draft_answers[key]['reviewed_by_id'] = user.pk
    app.approved_at, app.approved_by, app.approval_digest = None, None, ''
    app.status = 'ready_for_review'
    blockers = approval_blockers(app)
    app.status = 'review_required' if blockers else 'ready_for_review'
    if approve:
        if not confirmed:
            raise ValidationError('Explicit approval confirmation is required.')
        if blockers:
            raise ValidationError(blockers)
        app.status, app.approved_at, app.approved_by = 'approved', timezone.now(), user
        app.approval_digest = draft_digest(app)
    app.save()
    ApplicationEvent.objects.create(application=app, actor=user, action='approved_for_manual_opening' if approve else 'review_saved')
    return app
