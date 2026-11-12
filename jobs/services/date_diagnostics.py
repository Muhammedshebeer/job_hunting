"""Whitelisted, opt-in publication-date diagnostics; never raw source records."""
from jobs import qualification as policy
import re
from datetime import datetime, timedelta, timezone as utc_timezone
from zoneinfo import ZoneInfo

from django.utils import timezone

from jobs.services.discovery import ROLE_PATTERN


UAE = ZoneInfo('Asia/Dubai')
AMBIGUOUS_DATE_REASONS = {'modification_field_not_publication', 'publication_semantics_unverified'}


def mapped_publication_evidence(source):
    field = source.get('fields', {}).get('posted_at', '')
    # Configuration may not relabel update/fetch/email times as publication.
    parts = [re.sub(r'[^a-z0-9]', '', part.lower()) for part in field.split('.')]
    disallowed = {'updatedat', 'updated', 'modifiedat', 'modified', 'lastmodified', 'lastmodifiedat',
                  'lastupdated', 'lastupdatedat', 'fetchedat', 'retrievedat', 'discoveredat',
                  'receivedat', 'internaldate', 'emaildate', 'createdat'}
    if any(part in disallowed or part.startswith(('updated', 'modified', 'fetched', 'retrieved',
               'received', 'lastmodified', 'lastupdated', 'discovered')) for part in parts):
        return field, 'not_publication', 'modification_field_not_publication'
    semantics = source.get('publication_date_semantics')
    reference = source.get('publication_date_reference')
    if (semantics not in ('original_publication', 'last_publication')
            or not isinstance(reference, str) or not reference.strip() or reference.startswith('REPLACE_')):
        return field, 'unverified', 'publication_semantics_unverified'
    return field, semantics, None


def assess_date(value, now):
    if value is None or value == '':
        return None, 'missing_publication_date'
    if not isinstance(value, str):
        return None, 'invalid_publication_date'
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None, 'ambiguous_date_only'
    try:
        if 'T' not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, OverflowError):
        return None, 'invalid_publication_date'
    if timezone.is_naive(parsed):
        return None, 'ambiguous_timezone'
    if parsed > now:
        return parsed, 'future_publication_date'
    if parsed < now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS):
        return parsed, 'older_than_publication_window'
    return parsed, 'within_publication_window'


def safe_job_text(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'https?://\S+|\b[^\s@]+@[^\s@]+\b', '[redacted]', value)
    return re.sub(r'\s+', ' ', value).strip()[:250]


def date_window(now):
    cutoff = now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS)
    return {'timezone': 'Asia/Dubai', 'run_utc': now.astimezone(utc_timezone.utc).isoformat(),
            'run_uae': now.astimezone(UAE).isoformat(),
            'cutoff_utc': cutoff.astimezone(utc_timezone.utc).isoformat(),
            'cutoff_uae': cutoff.astimezone(UAE).isoformat(),
            'inclusive_window_hours': 24 * policy.PUBLICATION_WINDOW_DAYS}


def candidate_date_diagnostic(row, source, now):
    if not isinstance(row, dict) or not isinstance(row.get('title'), str):
        return None
    if not ROLE_PATTERN.search(row['title']):
        return None
    adapter = source['adapter']
    field, semantics = {
        'greenhouse': ('first_published', 'original_publication'),
        'ashby': ('publishedAt', 'last_publication'),
        'mapped_json': ('configured_posted_at_mapping', 'configuration_dependent'),
        'email_alerts': ('email_job_specific_posting_field', 'sender_asserted_publication'),
    }.get(adapter, ('posted_at', 'supplied_publication'))
    parsed, reason = assess_date(row.get('posted_at'), now)
    ignored = None
    if adapter == 'mapped_json':
        field, semantics, ambiguous = mapped_publication_evidence(source)
        if ambiguous:
            ignored, parsed, reason = parsed, None, ambiguous
    elif adapter == 'email_alerts':
        field = ('JobPosting.datePosted' if row.get('date_evidence') == 'email JobPosting.datePosted'
                 else 'explicit_job_posting_label')
    return {'source': source['name'], 'company': safe_job_text(row.get('company')),
            'title': safe_job_text(row.get('title')), 'location': safe_job_text(row.get('location')),
            'publication_field': field, 'publication_semantics': semantics,
            'parsed_utc': parsed.astimezone(utc_timezone.utc).isoformat() if parsed else None,
            'parsed_uae': parsed.astimezone(UAE).isoformat() if parsed else None,
            'age_days': round((now - parsed).total_seconds() / 86400, 3) if parsed else None,
            'ignored_unverified_timestamp_utc': ignored.astimezone(utc_timezone.utc).isoformat() if ignored else None,
            'date_result': reason}
