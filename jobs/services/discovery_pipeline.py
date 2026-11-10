"""Phase 2C: shared qualification/review and insert-only, cross-source ingestion."""
from jobs import qualification as policy
import re
from collections import Counter

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job
from jobs.services.alert_mailbox import PLATFORM_DOMAINS
from jobs.services.discovery import (DiscoveryError, canonical_url, ROLE_PATTERN, target_location,
                                     experience, posted_within_week)
from jobs.services.job_alerts import classify_alert, job_identity, parse_publication
from jobs.services.matching import score_job
from jobs.services.date_diagnostics import AMBIGUOUS_DATE_REASONS
from jobs.services.discovery_lock import check_discovery_ownership


def filter_funnel(row, cv_text, now):
    """Read-only diagnostics; never changes qualification decisions."""
    counts = Counter({key: 0 for key in (
        'stage.role_pass', 'stage.uae_pass', 'stage.date_pass', 'stage.experience_pass',
        'stage.cv_pass', 'stage.eligible', 'audit.role_matches', 'audit.uae_matches', 'audit.uae_role_matches',
        'audit.recent_matches', 'stage.role_unknown', 'stage.uae_unknown',
        'stage.date_unknown', 'stage.experience_unknown', 'stage.description_unknown')})
    if not isinstance(row, dict):
        return counts
    title, location, description = (row.get(key) for key in ('title', 'location', 'description'))
    role = isinstance(title, str) and bool(ROLE_PATTERN.search(title))
    uae = target_location(location)
    reason = row.get('_publication_date_reason')
    ambiguous_date = isinstance(reason, str) and reason in AMBIGUOUS_DATE_REASONS
    recent = not ambiguous_date and posted_within_week(row.get('posted_at'), now) is not None
    counts.update({'audit.role_matches': int(role), 'audit.uae_matches': int(uae),
                   'audit.uae_role_matches': int(role and uae), 'audit.recent_matches': int(recent)})
    if not role:
        counts['stage.role_unknown'] += int(not isinstance(title, str) or not title.strip())
        return counts
    counts['stage.role_pass'] += 1
    if not uae:
        counts['stage.uae_unknown'] += int(not isinstance(location, str) or not location.strip())
        return counts
    counts['stage.uae_pass'] += 1
    if not recent:
        counts['stage.date_unknown'] += int(ambiguous_date or parse_publication(row.get('posted_at')) is None)
        return counts
    counts['stage.date_pass'] += 1
    if not isinstance(description, str):
        description = ''
    bounds = experience({**row, 'description': description})
    if bounds is None or bounds[0] is None:
        counts['stage.experience_unknown'] += 1
        return counts
    if bounds[0] > 3 or bounds[1] is not None and bounds[1] < 1:
        return counts
    counts['stage.experience_pass'] += 1
    if not description.strip():
        counts['stage.description_unknown'] += 1
        return counts
    score, _ = score_job(description, cv_text)
    if score >= policy.MIN_CV_SCORE:
        counts['stage.cv_pass'] += 1
    return counts


def normalized_identity(url):
    for platform in PLATFORM_DOMAINS:
        identity = job_identity(url, platform)
        if identity:
            return identity[0]
    return canonical_url(url)


def employer_identity(data):
    domain, identity = data.get('employer_domain'), data.get('employer_job_id')
    if (isinstance(domain, str) and re.fullmatch(r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', domain)
            and isinstance(identity, str) and 0 < len(identity.strip()) <= 250):
        return domain.lower(), identity.strip()
    return None


class IdentityIndex:
    """One snapshot + run-local reservations, also used by combined dry runs."""
    def __init__(self):
        self.urls, self.ids, self.employers = set(), set(), set()
        for source, source_id, url, metadata in Job.objects.values_list(
                'source', 'source_id', 'url', 'discovery_metadata').iterator(chunk_size=2000):
            self.remember({**(metadata if isinstance(metadata, dict) else {}),
                           'source': source, 'source_id': source_id, 'url': url})

    def contains(self, data):
        try:
            url = normalized_identity(data.get('url'))
        except (DiscoveryError, ValueError, TypeError):
            url = None
        source_id = data.get('source_id')
        identity = (data.get('source'), str(source_id) if type(source_id) in (str, int) else '')
        employer = employer_identity(data)
        return bool(url in self.urls or identity[1] and identity in self.ids
                    or employer and employer in self.employers)

    def remember(self, data):
        try:
            self.urls.add(normalized_identity(data.get('url')))
        except (DiscoveryError, ValueError, TypeError):
            pass
        if data.get('source_id'):
            self.ids.add((data.get('source'), data['source_id']))
        employer = employer_identity(data)
        if employer:
            self.employers.add(employer)


def classify_record(row, source, cv_text, now, *, method, complete_description=False):
    if not isinstance(row, dict):
        return None, 'invalid_record'
    row = dict(row)
    try:
        row['url'] = normalized_identity(row.get('url'))
    except (DiscoveryError, ValueError, TypeError):
        return None, 'invalid_url'
    for field in ('title', 'company', 'location', 'description'):
        value = row.get(field)
        if value is not None and not isinstance(value, str):
            return None, 'invalid_fields'
        row[field] = (value or '').strip()
    ambiguous_date = row.get('_publication_date_reason')
    ambiguous_date = ambiguous_date if isinstance(ambiguous_date, str) and ambiguous_date in AMBIGUOUS_DATE_REASONS else None
    if ambiguous_date:
        row['posted_at'] = None
    row['source'] = source
    source_id = row.get('source_id')
    row['source_id'] = str(source_id) if type(source_id) in (str, int) else ''
    row['description_verified'] = bool(row['description']) and (
        row.get('description_verified') is True or row.get('description_complete') is True or complete_description)
    valid_through = parse_publication(row.get('valid_through'))
    if row.get('accepting_applications') is False or valid_through and valid_through < now:
        return None, 'rejected_closed'
    data, outcome = classify_alert(row, cv_text, now)
    if data is None:
        return None, outcome
    # The public feed/manual record may explicitly establish open status.
    # Do not synthesize validThrough or a publication timestamp to satisfy it.
    if method != 'email_alert' and row.get('accepting_applications') is True:
        data['review_reasons'] = [reason for reason in data['review_reasons']
                                  if reason != 'accepting_applications_unverified']
        data['requires_review'] = bool(data['review_reasons'])
        data['accepting_applications'] = not data['requires_review']
        outcome = 'review' if data['requires_review'] else 'qualified'
    data['discovery_metadata']['method'] = method
    for flag in ('synthetic', 'is_synthetic', 'demo', 'test_fixture'):
        if row.get(flag) is True:
            data['discovery_metadata'][flag] = True
    if ambiguous_date:
        data['discovery_metadata']['publication_date_ignored_reason'] = ambiguous_date
        data['discovery_metadata']['publication_date_field'] = row.get('_publication_field', '')
        data['review_reasons'].append(ambiguous_date)
        data['requires_review'] = True
        data['accepting_applications'] = False
        outcome = 'review'
    data['discovery_metadata']['date_verified_at_source'] = bool(data['posted_at']) and method in ('greenhouse', 'ashby')
    if data['posted_at'] and method != 'email_alert':
        data['discovery_metadata']['date_evidence'] = (
            'manual posting-date evidence' if method in ('csv', 'manual_link', 'manual_links') else 'configured API publication field')
        data['discovery_metadata']['publication_date_field'] = {
            'greenhouse': 'first_published', 'ashby': 'publishedAt',
            'mapped_json': row.get('_publication_field', 'configured_posted_at_mapping'),
        }.get(method, 'posted_at')
        data['discovery_metadata']['publication_date_semantics'] = {
            'greenhouse': 'original_publication', 'ashby': 'last_publication',
            'mapped_json': row.get('_publication_semantics', 'unverified'),
        }.get(method, 'supplied_publication')
    employer = employer_identity(row)
    if employer:
        data['discovery_metadata'].update(employer_domain=employer[0], employer_job_id=employer[1])
    return data, outcome


def ingest_rows(rows, source, cv_text, *, method, dry_run=False, index=None, now=None,
                complete_description=False, platform_rows=False, on_rejected=None):
    if dry_run and on_rejected is not None:
        raise DiscoveryError('Persistent discovery observers are not allowed in a dry run')
    index = index if index is not None else IdentityIndex()
    now = now or timezone.now()
    counts = Counter({key: 0 for key in ('fetched', 'eligible', 'review', 'rejected', 'duplicates', 'created', 'would_create')})
    for row in rows:
        check_discovery_ownership()
        counts['fetched'] += 1
        counts.update(filter_funnel(row, cv_text, now))
        origin = row.get('source') if isinstance(row, dict) and platform_rows else source
        if platform_rows and (not isinstance(origin, str) or origin not in PLATFORM_DOMAINS):
            counts['rejected'] += 1
            counts['invalid_platform'] += 1
            if on_rejected is not None:
                on_rejected(row, source, 'invalid_platform')
            continue
        if platform_rows and method != 'email_alert' and not job_identity(row.get('url'), origin):
            counts['rejected'] += 1
            counts['unverified_job_link'] += 1
            if on_rejected is not None:
                on_rejected(row, origin, 'unverified_job_link')
            continue
        if isinstance(row, dict) and index.contains({**row, 'source': origin}):
            counts['duplicates'] += 1
            continue
        data, outcome = classify_record(row, origin, cv_text, now, method=method,
                                        complete_description=complete_description)
        if data is None:
            counts['rejected'] += 1
            counts[outcome] += 1
            if on_rejected is not None:
                on_rejected(row, origin, outcome)
            continue
        counts['eligible' if outcome == 'qualified' else 'review'] += 1
        counts['stage.eligible'] += int(outcome == 'qualified')
        for reason in data['review_reasons']:
            counts[reason] += 1
        if dry_run:
            index.remember({**data, **data['discovery_metadata']})
            counts['would_create'] += 1
        else:
            try:
                with transaction.atomic():
                    conflict = Q(url=data['url'])
                    if data['source_id']:
                        conflict |= Q(source=data['source'], source_id=data['source_id'])
                    if Job.objects.filter(conflict).exists():
                        counts['duplicates'] += 1
                        continue
                    Job.objects.create(**data)
                index.remember({**data, **data['discovery_metadata']})
                counts['created'] += 1
            except IntegrityError:
                counts['duplicates'] += 1
    return counts
