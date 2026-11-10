"""Explicit adapter registry; no code imports or arbitrary callbacks from JSON."""
import csv
import json
import re
from collections import Counter
from pathlib import Path
from django.utils import timezone

from jobs.services.alert_mailbox import PLATFORM_DOMAINS, MailboxError, iter_alert_messages, load_mailbox_config
from jobs.services.discovery import DiscoveryError, fetch_json, source_rows, validate_url
from jobs.services.job_alerts import parse_alert
from jobs.services.discovery_lock import exclusive_discovery, check_discovery_ownership


ADAPTERS = {}


def register_adapter(name, loader, validator):
    if name in ADAPTERS:
        raise ValueError('Adapter already registered')
    ADAPTERS[name] = (loader, validator)


def field_at(value, path):
    for key in path.split('.') if path else []:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def read_json(path):
    try:
        with open(path, encoding='utf-8-sig') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise DiscoveryError('Cannot read source JSON file; details suppressed') from None


def validate_company(source):
    if (not isinstance(source.get('board'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', source['board'])
            or not isinstance(source.get('company'), str) or not source['company'].strip()):
        raise DiscoveryError('Company adapter requires a valid board and company')


def validate_json(source):
    validate_url(source.get('url'))


def validate_mapping(source):
    validate_json(source)
    fields = source.get('fields')
    allowed = {'source_id', 'title', 'company', 'location', 'url', 'posted_at', 'description',
               'min_experience', 'max_experience', 'accepting_applications', 'valid_through',
               'employer_domain', 'employer_job_id', 'description_complete'}
    if not isinstance(fields, dict) or not fields or set(fields) - allowed:
        raise DiscoveryError('Mapped JSON fields are invalid')
    paths = [source.get('items_path', 'jobs'), *fields.values()]
    if any(not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.-]*', path) for path in paths):
        raise DiscoveryError('Only dot-separated object field paths are supported')


def validate_file(source):
    if not isinstance(source.get('path'), str) or not source['path'].strip():
        raise DiscoveryError('Local source requires a path')


def feed_rows(source, context):
    yield from source_rows(source, context['fetcher'])


def mapped_rows(source, context):
    from jobs.services.date_diagnostics import mapped_publication_evidence
    rows = field_at(context['fetcher'](source['url']), source.get('items_path', 'jobs'))
    if not isinstance(rows, list):
        raise DiscoveryError('Mapped JSON item path did not contain a list')
    for row in rows:
        if not isinstance(row, dict):
            yield None
            continue
        normalized = {key: field_at(row, path) for key, path in source['fields'].items()}
        field, semantics, ambiguous = mapped_publication_evidence(source)
        normalized.update(_publication_field=field, _publication_semantics=semantics,
                          _publication_date_reason=ambiguous)
        yield normalized


def csv_rows(source, context):
    try:
        with open(source['path'], encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or 'url' not in reader.fieldnames:
                raise DiscoveryError('CSV requires a url column')
            for row in reader:
                for field in ('min_experience', 'max_experience'):
                    value = (row.get(field) or '').strip()
                    row[field] = int(value) if value.isdigit() else (value or None)
                for field in ('accepting_applications', 'description_complete'):
                    row[field] = {'true': True, 'false': False}.get((row.get(field) or '').strip().lower())
                yield row
    except (OSError, csv.Error, UnicodeError):
        raise DiscoveryError('Cannot read authorized CSV; details suppressed') from None


def manual_rows(source, context):
    records = read_json(source['path'])
    if not isinstance(records, list):
        raise DiscoveryError('Manual-link file must contain a JSON list')
    yield from records


def email_rows(source, context):
    try:
        config = load_mailbox_config(source['path'])
        for platform in PLATFORM_DOMAINS:
            context['diagnostics'][f'platform.{platform}.configured_senders'] += len(config['senders'].get(platform, []))
        messages = iter_alert_messages(config, context['diagnostics'])
        try:
            for platform, message in messages:
                context['diagnostics'][f'platform.{platform}.messages_read'] += 1
                local = Counter()
                try:
                    yield from parse_alert(message, platform, local)
                finally:
                    context['diagnostics'].update(local)
                    context['diagnostics'].update({f'platform.{platform}.{key}': value for key, value in local.items()})
        finally:
            messages.close()
    except MailboxError:
        raise DiscoveryError('Authorized mailbox source failed; private details suppressed') from None


for _name in ('greenhouse', 'ashby'):
    register_adapter(_name, feed_rows, validate_company)
register_adapter('authorized_json', feed_rows, validate_json)
register_adapter('mapped_json', mapped_rows, validate_mapping)
register_adapter('csv', csv_rows, validate_file)
register_adapter('manual_links', manual_rows, validate_file)
register_adapter('email_alerts', email_rows, validate_file)


def load_plan(path):
    sources = read_json(path)
    if not isinstance(sources, list):
        raise DiscoveryError('Discovery plan must be a JSON list')
    names = set()
    base = Path(path).resolve().parent
    for source in sources:
        if not isinstance(source, dict):
            raise DiscoveryError('Invalid discovery source')
        name = source.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]{1,50}', name) or name in names:
            raise DiscoveryError('Discovery source names must be unique safe names, at most 50 characters')
        names.add(name)
        if type(source.get('enabled')) is not bool:
            raise DiscoveryError('Each source requires a boolean enabled flag')
        if not source['enabled']:
            continue
        adapter = source.get('adapter')
        if not isinstance(adapter, str) or adapter not in ADAPTERS:
            raise DiscoveryError('Unsupported discovery adapter')
        reference = source.get('authorization_reference')
        if source.get('authorized') is not True or not isinstance(reference, str) or not reference.strip():
            raise DiscoveryError('Every enabled source requires explicit authorization and a reference')
        ADAPTERS[adapter][1](source)
        if adapter in ('csv', 'manual_links', 'email_alerts'):
            source['path'] = str((base / source['path']).resolve())
    return sources


@exclusive_discovery
def run_plan(sources, cv_text, *, dry_run=False, fetcher=fetch_json, now=None, date_diagnostics=None,
             on_rejected=None, on_source=None):
    if date_diagnostics is not None and not dry_run:
        raise DiscoveryError('Publication-date diagnostics require a dry run')
    if dry_run and (on_rejected is not None or on_source is not None):
        raise DiscoveryError('Persistent discovery observers are not allowed in a dry run')
    from jobs.services.discovery_pipeline import IdentityIndex, ingest_rows
    index, totals, reports, errors = IdentityIndex(), Counter(), {}, []
    now = now or timezone.now()
    base_counts = {key: 0 for key in ('fetched', 'eligible', 'review', 'rejected', 'duplicates', 'created', 'would_create', 'failed_sources')}
    groups = {f'adapter.{name}': Counter(base_counts) for name in ('greenhouse', 'ashby')}
    groups.update({f'platform.{name}': Counter({**base_counts, 'messages_read': 0, 'configured_senders': 0}) for name in PLATFORM_DOMAINS})
    for source in sources:
        if not source['enabled']:
            continue
        name, adapter = source['name'], source['adapter']
        diagnostics = Counter()
        # Counters are updated incrementally even if a generator fails halfway.
        report = Counter({key: 0 for key in ('fetched', 'eligible', 'review', 'rejected', 'duplicates', 'created', 'would_create', 'failed_sources')})
        rows = None
        try:
            def guarded_fetch(url):
                check_discovery_ownership()
                return fetcher(url)
            check_discovery_ownership()
            rows = ADAPTERS[adapter][0](source, {'fetcher': guarded_fetch, 'diagnostics': diagnostics})
            for row in rows:
                if date_diagnostics is not None:
                    from jobs.services.date_diagnostics import candidate_date_diagnostic
                    item = candidate_date_diagnostic(row, source, now)
                    if item is not None:
                        date_diagnostics.append(item)
                row_counts = ingest_rows([row], name, cv_text, method='email_alert' if adapter == 'email_alerts' else adapter,
                    dry_run=dry_run, index=index, now=now,
                    complete_description=adapter in ('greenhouse', 'ashby'),
                    platform_rows=adapter in ('csv', 'manual_links', 'email_alerts'), on_rejected=on_rejected)
                report.update(row_counts)
                if (adapter in ('csv', 'manual_links', 'email_alerts') and isinstance(row, dict)
                        and isinstance(row.get('source'), str) and row['source'] in PLATFORM_DOMAINS):
                    groups[f"platform.{row['source']}"].update(row_counts)
        except (DiscoveryError, ValueError, TypeError, AttributeError):
            errors.append(name)
            report['failed_sources'] += 1
        finally:
            if hasattr(rows, 'close'):
                rows.close()
        report.update({'adapter.' + key: value for key, value in diagnostics.items()})
        if f'adapter.{adapter}' in groups:
            groups[f'adapter.{adapter}'].update(report)
        for platform in PLATFORM_DOMAINS:
            prefix = f'platform.{platform}.'
            groups[f'platform.{platform}'].update({key[len(prefix):]: value for key, value in diagnostics.items()
                                                  if key.startswith(prefix)})
        reports[name] = report
        totals.update(report)
        if on_source is not None:
            check_discovery_ownership()
            on_source(name, dict(report), dict(totals))
    reports.update(groups)
    return totals, reports, errors
