"""Read-only discovery over explicitly configured public/authorized JSON APIs.

No browser login, scraping, applications, CV uploads, or environment secrets.
"""
from jobs import qualification as policy
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.utils import timezone

from jobs.models import Job
from jobs.services.matching import score_job


class DiscoveryError(ValueError):
    """Safe-to-display failure with no remote response, URL, or credentials."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DiscoveryError('Redirect refused; configure the final authorized endpoint')


def validate_url(value):
    if not isinstance(value, str):
        raise DiscoveryError('Missing or invalid HTTPS URL')
    try:
        parts = urlsplit(value)
        URLValidator(schemes=['https'])(value)
        if parts.username or parts.password or parts.fragment:
            raise ValueError
        # Reject credentials in feed URLs; adapters support public feeds only.
        if any(re.search(r'token|secret|password|api.?key|signature', key, re.I)
               for key, _ in parse_qsl(parts.query)):
            raise ValueError
        return parts
    except (ValueError, ValidationError):
        raise DiscoveryError('Invalid HTTPS URL or embedded credentials') from None


def canonical_url(value):
    parts = validate_url(value)
    # Preserve identity-bearing parameters (e.g. jobId, jk); remove only tracking.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'trk', 'trackingid'}]
    return urlunsplit(('https', parts.netloc.lower(), parts.path or '/', urlencode(sorted(query)), ''))


def fetch_json(url):
    validate_url(url)
    request = Request(url, headers={
        'Accept': 'application/json', 'User-Agent': 'UAEJobAgent/phase2 (job discovery)',
    })
    try:
        # No environment proxies, implicit authentication, or redirected requests.
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=20) as response:
            if response.headers.get_content_type() != 'application/json':
                raise DiscoveryError('Expected application/json')
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise DiscoveryError('Response exceeds 5 MB limit')
            return json.loads(body)
    except DiscoveryError:
        raise
    except HTTPError as exc:
        raise DiscoveryError(f'HTTP {exc.code}; no retry or bypass attempted') from None
    except (URLError, TimeoutError, OSError, UnicodeError, ValueError):
        raise DiscoveryError('Network failure or invalid JSON') from None


def load_sources(path):
    try:
        with open(path, encoding='utf-8') as handle:
            sources = json.load(handle)
    except (OSError, ValueError):
        raise DiscoveryError('Cannot read discovery configuration JSON') from None
    if not isinstance(sources, list):
        raise DiscoveryError('Configuration must be a JSON list')
    names = set()
    for source in sources:
        if not isinstance(source, dict) or type(source.get('enabled')) is not bool:
            raise DiscoveryError('Each source requires a boolean enabled flag')
        name = source.get('name', '')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]{1,50}', name) or name in names:
            raise DiscoveryError('Source names must be unique, lowercase, and at most 50 characters')
        names.add(name)
        if not source['enabled']:
            continue
        if source.get('adapter') not in ('greenhouse', 'ashby', 'authorized_json'):
            raise DiscoveryError('Unsupported adapter')
        reference = source.get('authorization_reference')
        if source.get('authorized') is not True or not isinstance(reference, str) or not reference.strip():
            raise DiscoveryError(f'{name}: authorization and a reference are required')
        if source['adapter'] in {'greenhouse', 'ashby'}:
            board = source.get('board')
            if not isinstance(board, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', board):
                raise DiscoveryError(f'{name}: invalid board identifier')
            if not isinstance(source.get('company'), str) or not source['company'].strip():
                raise DiscoveryError(f'{name}: company is required')
        else:
            validate_url(source.get('url'))
    return sources


def plain_text(value):
    if not isinstance(value, str):
        return ''
    # Greenhouse can return escaped HTML inside JSON content.
    return unescape(re.sub(r'<[^>]*>', ' ', unescape(unescape(value))))


def source_rows(source, fetcher=fetch_json):
    """Return normalized records; never substitute updated_at for publication."""
    adapter = source['adapter']
    if adapter == 'greenhouse':
        url = f"https://boards-api.greenhouse.io/v1/boards/{source['board']}/jobs?content=true"
    elif adapter == 'ashby':
        url = f"https://api.ashbyhq.com/posting-api/job-board/{source['board']}"
    else:
        url = source['url']
    payload = fetcher(url)
    rows = payload.get('jobs') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise DiscoveryError('Expected a JSON object containing a jobs list')
    detail_requests = 0
    for row in rows:
        if not isinstance(row, dict):
            yield None
            continue
        if adapter == 'authorized_json':
            yield row
        elif adapter == 'greenhouse':
            location = row.get('location')
            location_name = location.get('name') if isinstance(location, dict) else None
            job_id = row.get('id')
            if type(job_id) not in (str, int) or not re.fullmatch(r'\d+', str(job_id)):
                yield None
                continue
            # The documented list response may omit first_published. Retrieve
            # detail only for target roles/locations, bounded to 100 per source.
            if (not row.get('first_published') and isinstance(row.get('title'), str)
                    and ROLE_PATTERN.search(row['title']) and target_location(location_name)):
                if detail_requests >= 100:
                    raise DiscoveryError('100-job detail request limit reached; narrow the employer board')
                detail_requests += 1
                detail = fetcher(f"https://boards-api.greenhouse.io/v1/boards/{source['board']}/jobs/{job_id}")
                if not isinstance(detail, dict) or str(detail.get('id')) != str(job_id):
                    yield None
                    continue
                row = detail
                location = row.get('location')
            yield {
                'source_id': str(job_id), 'company': source['company'],
                'title': row.get('title'), 'location': location.get('name') if isinstance(location, dict) else None,
                'url': row.get('absolute_url'), 'posted_at': row.get('first_published'),
                'description': plain_text(row.get('content')),
                'accepting_applications': True,
            }
        else:
            job_url = row.get('jobUrl')
            source_id = row.get('id')
            if not source_id and isinstance(job_url, str):
                source_id = job_url.split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1]
            location = row.get('location')
            if row.get('workplaceType') == 'Remote' and isinstance(location, str):
                location += ' (Remote)'
            yield {
                'source_id': source_id,
                'company': source['company'], 'title': row.get('title'),
                'location': location, 'url': job_url,
                'posted_at': row.get('publishedAt'), 'description': row.get('descriptionPlain'),
                'accepting_applications': row.get('isListed') is True,
            }


def posted_within_window(value, now):
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        posted = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if timezone.is_naive(posted) or not now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS) <= posted <= now:
            return None
        return posted
    except (ValueError, OverflowError):
        return None


ROLE_PATTERN = re.compile(
    r'\b(?:python|django|back[ -]?end|full[ -]?stack|software engineer|'
    r'ai developer|ai engineer|artificial intelligence developer)\b', re.I,
)
CITY_PATTERN = re.compile(r'\b(?:dubai|abu dhabi|sharjah)\b', re.I)
UAE_PATTERN = re.compile(r'\b(?:uae|u\.a\.e\.?|united arab emirates)\b', re.I)
EXPERIENCE_RANGE = re.compile(
    r'\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s+years?\s+(?:of\s+)?experience\b', re.I,
)
EXPERIENCE_MIN = re.compile(r'\b(\d{1,2})\s*(\+)?\s+years?\s+(?:of\s+)?experience\b', re.I)


def target_location(location):
    return isinstance(location, str) and bool(CITY_PATTERN.search(location) or (
        UAE_PATTERN.search(location) and re.search(r'\bremote\b', location, re.I)
    ))


def experience(row):
    low, high = row.get('min_experience'), row.get('max_experience')
    if low is not None or high is not None:
        if any(x is not None and (type(x) is not int or not 0 <= x <= 100) for x in (low, high)):
            return None
        if low is not None and high is not None and low > high:
            return None
        return low, high
    # Conservative extraction; conflicting statements need manual review.
    description = row['description']
    ranges = EXPERIENCE_RANGE.findall(description)
    singles = EXPERIENCE_MIN.findall(EXPERIENCE_RANGE.sub('', description))
    requirements = [(int(a), int(b)) for a, b in ranges]
    requirements += [(int(n), None if plus else int(n)) for n, plus in singles]
    if len(set(requirements)) != 1:
        return None
    low, high = requirements[0]
    return (low, high) if high is None or low <= high else None


def normalize(row, source_name, cv_text, now):
    if not isinstance(row, dict):
        return None, 'invalid_record'
    for field in ('company', 'title', 'location', 'description'):
        if not isinstance(row.get(field), str) or not row[field].strip():
            return None, 'missing_fields'
    posted = posted_within_window(row.get('posted_at'), now)
    if not posted:
        return None, 'unverified_or_outside_publication_window'
    if row.get('accepting_applications') is not True:
        return None, 'closed_or_unverified_open'
    if not ROLE_PATTERN.search(row['title']):
        return None, 'role'
    if not target_location(row['location']):
        return None, 'location'
    bounds = experience(row)
    if bounds is None:
        return None, 'experience_needs_review'
    low, high = bounds
    if low is None or low > 3 or (high is not None and high < 1):
        return None, 'experience'
    score, _ = score_job(row['description'], cv_text)
    if score < policy.MIN_CV_SCORE:
        return None, 'match_below_threshold'
    try:
        url = canonical_url(row.get('url'))
    except DiscoveryError:
        return None, 'invalid_url'
    source_id = row.get('source_id', '')
    if not isinstance(source_id, str) or not source_id.strip():
        return None, 'missing_source_id'
    data = {key: row[key].strip() for key in ('company', 'title', 'location', 'description')}
    data.update(source=source_name, source_id=source_id.strip(), url=url, posted_at=posted,
                min_experience=low, max_experience=high, match_score=score,
                accepting_applications=True)
    candidate = Job(**data)
    try:
        candidate.clean_fields()
    except ValidationError:
        return None, 'invalid_fields'
    return data, None


def measure_discovery_row(row, cv_text, now):
    """Independent aggregate diagnostics; never includes candidate text."""
    metrics = Counter(fetched=1, recent=0, uae_location=0, recent_uae=0,
                      uae_target_role=0, uae_target_cv_pass=0,
                      uae_target_experience_unverified=0,
                      recent_uae_target_role=0, recent_uae_target_cv_pass=0,
                      recent_uae_target_experience_unverified=0,
                      missing_publication_date=0, invalid_publication_date=0,
                      older_than_publication_window=0, future_publication_date=0)
    if not isinstance(row, dict):
        return metrics
    value = row.get('posted_at')
    posted = None
    if not value:
        metrics['missing_publication_date'] += 1
    else:
        try:
            if not isinstance(value, str) or 'T' not in value:
                raise ValueError
            posted = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if timezone.is_naive(posted):
                raise ValueError
        except (ValueError, OverflowError):
            posted = None
            metrics['invalid_publication_date'] += 1
    if posted is not None:
        if posted > now:
            metrics['future_publication_date'] += 1
        elif posted < now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS):
            metrics['older_than_publication_window'] += 1
        else:
            metrics['recent'] += 1
    if not target_location(row.get('location')):
        return metrics
    metrics['uae_location'] += 1
    if metrics['recent']:
        metrics['recent_uae'] += 1
    title = row.get('title')
    if not isinstance(title, str) or not ROLE_PATTERN.search(title):
        return metrics
    metrics['uae_target_role'] += 1
    if metrics['recent']:
        metrics['recent_uae_target_role'] += 1
    if not isinstance(row.get('description'), str):
        return metrics
    if experience(row) is None:
        metrics['uae_target_experience_unverified'] += 1
        if metrics['recent']:
            metrics['recent_uae_target_experience_unverified'] += 1
    score, _ = score_job(row['description'], cv_text)
    if score >= policy.MIN_CV_SCORE:
        metrics['uae_target_cv_pass'] += 1
        if metrics['recent']:
            metrics['recent_uae_target_cv_pass'] += 1
    return metrics


def discover(sources, cv_text, *, dry_run=False, now=None, fetcher=fetch_json):
    from jobs.services.discovery_pipeline import IdentityIndex
    shared_identities = IdentityIndex()
    now = now or timezone.now()
    counts = Counter()
    errors = []
    seen_ids, seen_urls = set(), set()
    # Legacy CSV/admin records may still include tracking parameters. Compare
    # canonical forms without rewriting those existing records.
    existing_urls = set()
    for url in Job.objects.values_list('url', flat=True).iterator(chunk_size=2000):
        try:
            existing_urls.add(canonical_url(url))
        except DiscoveryError:
            continue
    for source in sources:
        if not source['enabled']:
            counts['disabled_sources'] += 1
            continue
        try:
            for row in source_rows(source, fetcher):
                counts['seen'] += 1
                metrics = measure_discovery_row(row, cv_text, now)
                counts.update(metrics)
                counts.update({f"source.{source['name']}.{key}": value for key, value in metrics.items()})
                try:
                    data, reason = normalize(row, source['name'], cv_text, now)
                except (TypeError, ValueError, AttributeError):
                    counts['invalid_record'] += 1
                    continue
                if reason:
                    counts[reason] += 1
                    continue
                identity = (data['source'], data['source_id'])
                if (identity in seen_ids or data['url'] in seen_urls or data['url'] in existing_urls
                        or shared_identities.contains(data)):
                    counts['duplicates'] += 1
                    continue
                seen_ids.add(identity)
                seen_urls.add(data['url'])
                shared_identities.remember(data)
                if (Job.objects.filter(source=data['source'], source_id=data['source_id']).exists()
                        or Job.objects.filter(url=data['url']).exists()):
                    counts['duplicates'] += 1
                    continue
                if dry_run:
                    counts['would_create'] += 1
                else:
                    try:
                        with transaction.atomic():
                            Job.objects.create(**data)
                        counts['created'] += 1
                    except IntegrityError:
                        counts['duplicates'] += 1
        except DiscoveryError as exc:
            errors.append((source['name'], str(exc)))
            counts['failed_sources'] += 1
    return counts, errors


# Backwards-compatible import; uses the current centralized publication window.
posted_within_week = posted_within_window
