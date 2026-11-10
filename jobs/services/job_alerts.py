"""Conservative email extraction. No link fetching, redirects, or remote images."""
from jobs import qualification as policy
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from jobs.models import Job
from jobs.services.alert_mailbox import PLATFORM_DOMAINS, platform_domain
from jobs.services.discovery import (
    DiscoveryError, ROLE_PATTERN, canonical_url, experience, plain_text, target_location,
)
from jobs.services.matching import score_job


def direct_job_identity(url, source):
    """Recognized job-detail routes only. Never follows email tracking redirects."""
    if not isinstance(url, str) or source not in PLATFORM_DOMAINS:
        return None
    try:
        parts = urlsplit(url.strip())
        if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
                or not parts.hostname):
            return None
        ats = ats_job_identity(parts)
        if ats:
            return ats
        if not platform_domain(parts.hostname.lower(), source):
            return None
        query = parse_qs(parts.query, max_num_fields=64)
        if source == 'linkedin':
            match = re.search(r'/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d+)(?:/|$)', parts.path)
            identity = match.group(1) if match else ''
            if not identity and parts.path.rstrip('/') in ('/jobs/search', '/jobs/collections/recommended'):
                identity = (query.get('currentJobId') or [''])[0]
            if not re.fullmatch(r'\d+', identity):
                return None
            return f'https://www.linkedin.com/jobs/view/{identity}/', identity
        if source == 'indeed':
            identity = (query.get('jk') or [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9]{6,64}', identity) or parts.path.rstrip('/') not in ('/viewjob', '/rc/clk', '/rc/clk/dl', '/pagead/clk'):
                return None
            return f'https://www.indeed.com/viewjob?jk={identity}', identity
        if source == 'naukrigulf':
            match = re.search(r'(?:-jid-|-id-|-)(\d{8,})(?:/|$)', parts.path)
        else:
            match = re.search(r'/jobs/[^/]*-(\d+)(?:/|$)', parts.path)
        if not match:
            return None
        # Keep the source path; identity handles changed job slugs.
        return canonical_url(f'https://www.{PLATFORM_DOMAINS[source][0]}{parts.path}'), match.group(1)
    except (ValueError, DiscoveryError):
        return None


def ats_job_identity(parts):
    """Exact public ATS detail routes only, never general employer links."""
    host = parts.hostname.lower()
    if host in ('boards.greenhouse.io', 'job-boards.greenhouse.io'):
        match = re.fullmatch(r'/([A-Za-z0-9_-]{1,100})/jobs/(\d{1,30})/?', parts.path)
        if match:
            board, identity = match.groups()
            return (f'https://job-boards.greenhouse.io/{board}/jobs/{identity}',
                    f'greenhouse:{board}:{identity}')
    if host == 'jobs.ashbyhq.com':
        match = re.fullmatch(r'/([A-Za-z0-9_-]{1,100})/([a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})/?', parts.path)
        if match:
            board, identity = match.groups()
            identity = identity.lower()
            return f'https://jobs.ashbyhq.com/{board}/{identity}', f'ashby:{board}:{identity}'
    return None


DESTINATION_KEYS = {'url', 'u', 'r', 'dest', 'destination', 'redirect', 'redirect_url',
                    'redirecturl', 'target', 'joburl', 'job_url'}


def job_identity(url, source, _depth=0):
    """Resolve only literal same-platform destinations. Never fetch or decode tokens."""
    if not isinstance(url, str) or len(url) > 16000 or source not in PLATFORM_DOMAINS or _depth > 3:
        return None
    url = unescape(url.strip())
    identity = direct_job_identity(url, source)
    if identity:
        return identity
    try:
        outer = urlsplit(url)
        if (outer.scheme != 'https' or outer.username or outer.password
                or outer.port not in (None, 443)
                or not platform_domain((outer.hostname or '').lower(), source)):
            return None
        destinations = set()
        for key, values in parse_qs(outer.query, max_num_fields=64).items():
            if key.lower() not in DESTINATION_KEYS:
                continue
            for value in values:
                # Percent-encoded explicit URLs only; no base64/opaque token guessing.
                for _ in range(2):
                    if value.startswith('https://'):
                        break
                    value = unquote(value)
                resolved = job_identity(value, source, _depth + 1)
                if resolved:
                    destinations.add(resolved)
                else:
                    return None  # An ambiguous/unsafe destination fails closed.
        return next(iter(destinations)) if len(destinations) == 1 else None
    except (ValueError, DiscoveryError):
        return None


def record_link(url, source, counts, url_diagnostics=None):
    counts['links_examined'] += 1
    identity = job_identity(url, source)
    if url_diagnostics is not None:
        from jobs.services.link_diagnostics import embedded_shapes, url_shape
        url_diagnostics[url_shape(url, source, recognized=bool(identity))] += 1
        if not identity:
            for shape in embedded_shapes(url, source):
                url_diagnostics[(*shape[:4], 'embedded:' + shape[4])] += 1
    if identity:
        counts['recognized_job_links'] += 1
        if direct_job_identity(unescape(url.strip()), source) is None:
            counts['decoded_tracking_links'] += 1
    else:
        counts['rejected_links'] += 1
        try:
            parts = urlsplit(url)
            if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
                counts['rejected_unsafe_links'] += 1
            elif not platform_domain((parts.hostname or '').lower(), source):
                counts['rejected_external_links'] += 1
            elif (platform_domain((parts.hostname or '').lower(), source)
                    and re.search(r'track|click|redirect|alert|email|/rc/', parts.path, re.I)):
                counts['unresolved_tracking_links'] += 1
            else:
                counts['rejected_non_job_links'] += 1
        except ValueError:
            counts['rejected_unsafe_links'] += 1
    return identity


class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def text(self):
        output, stack = [], list(reversed(self.children))
        while stack:
            child = stack.pop()
            if isinstance(child, Node):
                stack.extend(reversed(child.children))
            else:
                output.append(child)
        return '\n'.join(output)

    def nodes(self):
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(child for child in reversed(node.children) if isinstance(child, Node))


class AlertHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def labeled(text, name):
    match = re.search(r'^[ \t]*' + name + r'[ \t]*:[ \t]*([^\n]+)$', text, re.I | re.M)
    return match.group(1).strip() if match else ''


def action_text(value):
    return bool(re.match(r'^(?:apply\b|view\b|see\b|read more\b|learn more\b|job details\b|click here\b|'
                         r'save job\b|show\b|find jobs\b|download\b|open\b|unsubscribe\b|manage alerts\b)', value.strip(), re.I))


def generic_email_text(value):
    return action_text(value) or bool(re.search(
        r'\b(?:job alerts?|recommended jobs|jobs for you|new jobs|welcome|preferences|'
        r'unsubscribe|confirm|thanks|thank you|find your next job)\b', value, re.I
    )) or value.strip().lower() in {'indeed', 'naukrigulf', 'linkedin', 'bayt', 'job description', 'full job description'}


def semantic_field(node):
    hint = ' '.join(node.attrs.get(key, '') for key in
                    ('class', 'itemprop', 'data-testid', 'data-test', 'aria-label')).lower()
    if re.search(r'company|hiringorganization|employer', hint):
        return 'company'
    if re.search(r'location|addresslocality', hint):
        return 'location'
    if re.search(r'description|snippet|summary', hint):
        return 'description'
    if re.search(r'job.?title|job.?name|^title$', hint):
        return 'title'
    return None


def compact_text(node):
    return re.sub(r'\s+', ' ', node.text()).strip()


def usable_excerpt(value):
    return (40 <= len(value) <= 5000 and not re.search(
        r'https?://|@|unsubscribe|privacy policy|sent to|manage.*alerts|email preferences', value, re.I))


def marked_listing(node):
    return node.tag in ('article', 'li') or bool(re.search(
        r'job[-_]?card|job[-_]?listing|job-list-item', node.attrs.get('class', ''), re.I))


def listing_card(node, identity, links):
    """Nearest useful listing context, bounded by both known and opaque peers."""
    card, best = node, node
    for _ in range(32):
        parent = card.parent
        if parent is None or parent.tag in ('body', 'html', '', 'header', 'footer', 'nav'):
            break
        descendants = list(parent.nodes())
        identities = {links.get(item) for item in descendants if item.tag == 'a'} - {None}
        if identities != {identity}:
            break
        current_nodes = set(card.nodes())
        if any(item not in current_nodes and (item.tag in ('header', 'footer', 'nav')
                   or item.tag in ('h1', 'h2', 'h3', 'h4') and generic_email_text(compact_text(item)))
               for item in descendants):
            break
        # Unresolved job-title links are boundaries, never newly accepted URLs.
        opaque_titles = {item.attrs.get('href') for item in descendants
                         if item.tag == 'a' and item.attrs.get('href') and not links.get(item)
                         and re.search(r'\b(?:developer|engineer|architect)\b', compact_text(item), re.I)
                         and not action_text(compact_text(item))}
        if opaque_titles and not (marked_listing(parent) and len(opaque_titles) == 1):
            break
        card = parent
        best = card
        if marked_listing(card):
            return card
        fields = {semantic_field(item) for item in descendants} - {None}
        text = card.text()
        has_company = 'company' in fields or bool(labeled(text, '(?:Company|Employer)'))
        has_location = 'location' in fields or bool(labeled(text, 'Location')) or any(
            re.fullmatch(r'(?:Dubai|Abu Dhabi|Sharjah)(?:\s*,\s*(?:UAE|United Arab Emirates))?', line.strip(), re.I)
            for line in text.splitlines())
        if has_company and has_location:
            return card
    return best


def html_card_row(node, card, url):
    """Extract only explicit/local evidence; no whole-email title guessing."""
    text = card.text()
    title, evidence = labeled(text, '(?:Job title|Title)'), 'label'
    if generic_email_text(title):
        title = ''
    if not title:
        title_node = next((item for item in card.nodes() if semantic_field(item) == 'title'
                           and compact_text(item) and not generic_email_text(compact_text(item))), None)
        if title_node:
            title, evidence = compact_text(title_node), 'semantic'
    if not title:
        heading = next((item for item in card.nodes() if item.tag in ('h1', 'h2', 'h3', 'h4')
                        and compact_text(item) and not generic_email_text(compact_text(item))), None)
        if heading:
            title, evidence = compact_text(heading), 'heading'
    if not title and compact_text(node) and not generic_email_text(compact_text(node)):
        title, evidence = compact_text(node), 'link'
    row = card_row(text, url, title, infer_title=False)
    row['title_evidence'] = evidence if row['title'] else 'missing'
    for field in ('company', 'location', 'description'):
        if not row[field]:
            value = next((compact_text(item) for item in card.nodes()
                          if semantic_field(item) == field and compact_text(item)), '')
            if value and len(value) <= {'company': 250, 'location': 200, 'description': 5000}[field]:
                row[field] = value
    if not row['description'] and marked_listing(card):
        paragraphs = [compact_text(item) for item in card.nodes() if item.tag == 'p'
                      and not any(child.tag == 'a' for child in item.nodes())]
        row['description'] = '\n'.join(value for value in paragraphs if usable_excerpt(value)
                                      and value not in (row['title'], row['company'], row['location']))[:5000]
    return row


def structured_records(value):
    if isinstance(value, list):
        for item in value:
            yield from structured_records(item)
    elif isinstance(value, dict):
        kind = value.get('@type')
        if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
            yield value
        yield from structured_records(value.get('@graph', []))


def structured_row(value):
    organization = value.get('hiringOrganization')
    company = organization.get('name', '') if isinstance(organization, dict) else ''
    company = company if isinstance(company, str) else ''
    title = value.get('title', '')
    title = title if isinstance(title, str) else ''
    locations = value.get('jobLocation', [])
    if isinstance(locations, dict):
        locations = [locations]
    names = []
    if isinstance(locations, list):
        for location in locations:
            address = location.get('address') if isinstance(location, dict) else None
            if isinstance(address, dict):
                names.append(', '.join(str(address.get(key, '')) for key in ('addressLocality', 'addressRegion', 'addressCountry') if address.get(key)))
    if value.get('jobLocationType') == 'TELECOMMUTE':
        restrictions = value.get('applicantLocationRequirements', [])
        if isinstance(restrictions, dict):
            restrictions = [restrictions]
        if isinstance(restrictions, list):
            names += [str(item.get('name', '')) + ' Remote' for item in restrictions if isinstance(item, dict)]
    requirements = value.get('experienceRequirements', '')
    extra = requirements if isinstance(requirements, str) else ''
    description = plain_text(value.get('description'))
    return {
        'url': value.get('url'), 'title': title, 'company': company,
        'location': '; '.join(names), 'posted_at': value.get('datePosted'),
        'description': description + ('\n' + extra if extra else ''),
        'description_verified': bool(description), 'valid_through': value.get('validThrough'),
        'date_evidence': 'email JobPosting.datePosted' if value.get('datePosted') else '',
    }


def card_row(text, url, title='', infer_title=True):
    title = labeled(text, 'Title') or title.strip()
    company, location = labeled(text, '(?:Company|Employer)'), labeled(text, 'Location')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not title and infer_title:
        title = next((line for line in lines if ROLE_PATTERN.search(line)
                      and len(line) <= 250 and not re.search(r'https?://|:', line)
                      and not re.search(r'\b(?:unsubscribe|alert|recommended|jobs for you)\b', line, re.I)), '')
    if not location:
        # Common alert layout: company and location separated by a bullet/dash.
        for index, line in enumerate(lines):
            parts = re.split(r'\s+[•·|–-]\s+', line, maxsplit=1)
            if len(parts) == 2 and target_location(parts[1]):
                company, location = company or parts[0], parts[1]
                break
            if target_location(line) and len(line) <= 200 and line != title:
                location = line
                if (not company and index and lines[index - 1] != title and len(lines[index - 1]) < 200
                        and not re.search(r'https?://|@|:|unsubscribe|alert', lines[index - 1], re.I)):
                    company = lines[index - 1]
                break
    # Never persist a surrounding email body, footer, or recipient information.
    excerpt = '\n'.join(filter(None, [labeled(text, 'Description'), labeled(text, 'Experience')]))
    if not excerpt and infer_title:
        excerpt = '\n'.join(line for line in lines if usable_excerpt(line)
                            and re.match(r'(?:You will|We are looking|Responsibilities|Build\b|Develop\b|Design\b)', line, re.I))[:5000]
    return {
        'url': url, 'title': title, 'company': company, 'location': location,
        'posted_at': labeled(text, '(?:Date posted|Posted on|Publication date)'),
        # Card text is kept only for the specific job, never the whole email.
        'description': excerpt, 'description_verified': False,
        'date_evidence': 'email explicit posting-date label',
    }


def body_parts(message, counts, depth=0):
    if depth > 20 or message.get_content_disposition() == 'attachment':
        counts['ignored_attachment_or_deep_part'] += 1
        return
    if message.get_content_type() == 'message/rfc822':
        return  # Do not descend into forwarded/attached messages.
    if message.is_multipart():
        for child in message.iter_parts():
            yield from body_parts(child, counts, depth + 1)
    else:
        yield message


def parse_alert(message, source, counts=None, url_diagnostics=None):
    """Parse both MIME alternatives, bounded HTML cards, and compact text digests."""
    counts = counts if counts is not None else Counter()
    html_parts, text_parts = [], []
    for part in body_parts(message, counts):
        if part.get_content_type() not in ('text/html', 'text/plain'):
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, ValueError):
            counts['mime_decode_failures'] += 1
            continue
        if isinstance(content, str):
            (html_parts if part.get_content_type() == 'text/html' else text_parts).append(content)
            counts['html_parts' if part.get_content_type() == 'text/html' else 'plain_text_parts'] += 1
    records = []
    for html in html_parts:
        parser = AlertHTML()
        try:
            parser.feed(html)
        except (ValueError, RecursionError):
            continue
        for node in parser.root.nodes():
            if node.tag == 'script' and node.attrs.get('type', '').lower() == 'application/ld+json':
                try:
                    for value in structured_records(json.loads(node.text())):
                        row = structured_row(value)
                        if record_link(row.get('url'), source, counts, url_diagnostics):
                            records.append(row)
                except (ValueError, TypeError, RecursionError):
                    counts['invalid_structured_data'] += 1
                    continue
        # Resolve each href once. This also avoids counting ancestor probes as links.
        links = {node: record_link(node.attrs.get('href'), source, counts, url_diagnostics)
                 for node in parser.root.nodes() if node.tag == 'a' and node.attrs.get('href')}
        for node in parser.root.nodes():
            if node not in links or not links[node]:
                continue
            identity = links[node]
            card = listing_card(node, identity, links)
            title = re.sub(r'\s+', ' ', node.text()).strip()
            counts['card_anchor_action_text' if action_text(title) else 'card_anchor_other_text'] += 1
            counts['card_anchor_role_match'] += int(bool(ROLE_PATTERN.search(title)))
            counts['card_anchor_generic_email_text'] += int(generic_email_text(title))
            counts['card_heading_role_match'] += int(any(
                item.tag in ('h1', 'h2', 'h3', 'h4') and ROLE_PATTERN.search(item.text()) for item in card.nodes()))
            row = html_card_row(node, card, identity[0])
            records.append(row)
    # Plain text is an independent alternative, even if HTML has no usable jobs.
    for text in text_parts:
        lines = text.splitlines()
        candidates = []
        for index, line in enumerate(lines):
            for url in re.findall(r'https://[^\s<>"\)]+', line):
                identity = record_link(url.rstrip('.,'), source, counts, url_diagnostics)
                if identity:
                    candidates.append((index, identity))
        # Digest conventions supported: metadata followed by its URL, or URL
        # followed by metadata. Blank lines or per-listing Title labels bound
        # cards; otherwise split a compact digest at job URLs (URL-last).
        previous_end = -1
        prefix = lines[:candidates[0][0]] if candidates else []
        url_first = not any(
            re.match(r'\s*(?:Title|Company|Location)\s*:', line, re.I)
            or (ROLE_PATTERN.search(line)
                and not re.search(r'\b(?:alert|recommended|new jobs|jobs for you)\b', line, re.I))
            for line in prefix
        )
        for index, (line_index, identity) in enumerate(candidates):
            next_link = candidates[index + 1][0] if index + 1 < len(candidates) else len(lines)
            start = previous_end + 1
            preceding = lines[start:line_index]
            has_metadata_before = not url_first and any(line.strip() for line in preceding)
            if has_metadata_before:
                end = line_index + 1
                block_lines = lines[start:end]
            else:
                end = next_link
                block_lines = lines[line_index:end]
            previous_end = end - 1
            records.append(card_row('\n'.join(block_lines), identity[0]))
    merged = {}
    # Prefer complete structured descriptions, then richer card alternatives.
    records.sort(key=lambda row: (bool(row.get('description_verified')),
                                 sum(bool(row.get(key)) for key in ('title', 'company', 'location', 'description'))), reverse=True)
    for row in records:
        identity = job_identity(row.get('url'), source)
        if identity is None:
            continue
        row.update(source=source, url=identity[0], source_id=identity[1])
        if identity[1] in merged:
            existing = merged[identity[1]]
            for key in ('title', 'company', 'location', 'description'):
                if not existing.get(key) and row.get(key):
                    existing[key] = row[key]
                    if key == 'title':
                        existing['title_evidence'] = row.get('title_evidence', 'alternative')
            continue
        merged[identity[1]] = row
    for row in merged.values():
        counts['extracted_job_candidates'] += 1
        counts['candidate_title_action_text'] += int(action_text(row.get('title', '')))
        counts['candidate_title_role_match'] += int(bool(ROLE_PATTERN.search(row.get('title', ''))))
        if row.get('title_evidence'):
            counts['candidate_title_evidence_' + row['title_evidence']] += 1
        for field in ('title', 'company', 'location', 'description'):
            if not row.get(field):
                counts['candidate_missing_' + field] += 1
        yield row


def parse_publication(value):
    # Email Date, IMAP INTERNALDATE and "N days ago" are never publication evidence.
    try:
        if not isinstance(value, str) or 'T' not in value:
            return None
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date if timezone.is_aware(date) else None
    except (ValueError, OverflowError):
        return None


def classify_alert(row, cv_text, now):
    reasons = []
    title, location = row.get('title', ''), row.get('location', '')
    if title and not ROLE_PATTERN.search(title):
        return None, 'rejected_role'
    if location and not target_location(location):
        return None, 'rejected_location'
    for field in ('title', 'company', 'location', 'description'):
        if not row.get(field):
            reasons.append('missing_' + field)
    posted = parse_publication(row.get('posted_at'))
    if posted is None:
        reasons.append('publication_date_unverified')
    elif posted > now or posted < now - timedelta(days=policy.PUBLICATION_WINDOW_DAYS):
        return None, 'rejected_date'
    bounds = experience(row)
    if bounds is None or bounds[0] is None:
        reasons.append('experience_unverified')
        bounds = (None, None)
    elif bounds[0] > 3 or bounds[1] is not None and bounds[1] < 1:
        return None, 'rejected_experience'
    score, _ = score_job(row['description'], cv_text)
    if not row.get('description_verified'):
        reasons.append('description_completeness_unverified')
    elif score < policy.MIN_CV_SCORE:
        return None, 'rejected_score'
    # Listing presence in an email alone does not establish that applications remain open.
    valid_through = parse_publication(row.get('valid_through'))
    open_verified = bool(valid_through and valid_through >= now)
    if not open_verified:
        reasons.append('accepting_applications_unverified')
    metadata = {
        'method': 'email_alert', 'date_evidence': row.get('date_evidence', '') if posted else '',
        'date_verified_at_source': False,  # No external job page has been requested.
        'description_evidence': 'email JobPosting' if row.get('description_verified') else 'email card excerpt',
        'valid_through': valid_through.isoformat() if valid_through else None,
    }
    date_text = row.get('posted_at')
    if isinstance(date_text, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T[^\s]{1,60})?', date_text):
        metadata['publication_date_text'] = date_text
    data = {key: row.get(key, '') for key in ('source', 'source_id', 'url', 'title', 'company', 'location', 'description')}
    data.update(posted_at=posted, min_experience=bounds[0], max_experience=bounds[1],
                match_score=score, requires_review=bool(reasons), review_reasons=reasons,
                accepting_applications=open_verified and not reasons, discovery_metadata=metadata)
    try:
        # Empty required text is deliberately retained for review, without inventing values.
        Job(**data).clean_fields(exclude=['title', 'company', 'location', 'description'])
        for field in ('title', 'company', 'location'):
            if len(data[field]) > Job._meta.get_field(field).max_length:
                raise ValueError
    except (ValidationError, ValueError, TypeError):
        return None, 'invalid_fields'
    return data, 'review' if reasons else 'qualified'


def import_alert_messages(messages, cv_text, *, dry_run=False, now=None, url_diagnostics=None):
    now = now or timezone.now()
    counts = Counter({key: 0 for key in (
        'links_examined', 'recognized_job_links', 'decoded_tracking_links', 'rejected_links',
        'unresolved_tracking_links', 'extracted_job_candidates', 'candidate_missing_title',
        'candidate_missing_company', 'candidate_missing_location', 'candidate_missing_description',
        'rejected_role', 'rejected_location', 'rejected_date', 'rejected_experience', 'rejected_score',
    )})
    existing_urls, existing_ids = set(), set()
    for source, source_id, url in Job.objects.values_list('source', 'source_id', 'url').iterator(chunk_size=2000):
        for platform in PLATFORM_DOMAINS:
            identity = job_identity(url, platform)
            if identity:
                existing_urls.add(identity[0])
                existing_ids.add((platform, identity[1]))
        existing_ids.add((source, source_id))
        try:
            existing_urls.add(canonical_url(url))
        except DiscoveryError:
            pass
    for source, message in messages:
        diagnostic = Counter()
        rows = list(parse_alert(message, source, diagnostic, url_diagnostics))
        counts.update(diagnostic)
        counts.update({f'source.{source}.{key}': value for key, value in diagnostic.items()})
        counts[f'source.{source}.messages_parsed'] += 1
        counts['messages_parsed'] += 1
        if not rows:
            counts['messages_without_recognized_jobs'] += 1
        for row in rows:
            counts['jobs_extracted'] += 1
            identity = (row['source'], row['source_id'])
            if identity in existing_ids or row['url'] in existing_urls:
                counts['duplicates'] += 1
                continue
            data, outcome = classify_alert(row, cv_text, now)
            if data is None:
                counts[outcome] += 1
                counts[f'source.{source}.{outcome}'] += 1
                continue
            for reason in data['review_reasons']:
                counts[reason] += 1
            # Store a hash only, never the raw email, subject, or message headers.
            message_id = str(message.get('Message-ID', ''))
            data['discovery_metadata']['message_id_sha256'] = hashlib.sha256(message_id.encode()).hexdigest() if message_id else ''
            existing_ids.add(identity)
            existing_urls.add(row['url'])
            if dry_run:
                counts['would_create_' + outcome] += 1
            else:
                try:
                    with transaction.atomic():
                        # Handles a concurrent insert since the initial identity snapshot.
                        if Job.objects.filter(Q(url=data['url']) | Q(source=source, source_id=data['source_id'])).exists():
                            counts['duplicates'] += 1
                            continue
                        Job.objects.create(**data)
                    counts['created_' + outcome] += 1
                except IntegrityError:
                    counts['duplicates'] += 1
    return counts
