"""Opt-in URL shape diagnostics. Never retain URL/query/path values."""
import re
from urllib.parse import parse_qs, unquote, urlsplit

from jobs.services.alert_mailbox import PLATFORM_DOMAINS, platform_domain


SAFE_SEGMENTS = frozenset('viewjob rc clk pagead jobs view comm email redirect click track tracking '
                          'mail mailer mailers job apply unsubscribe account search ja myjobs '
                          'jobalerts m j c u r v ng index.php jobdetails jobdetail job-details '
                          'job_detail myng jobseeker servlet trunk public en uae ae dl '
                          'my mypreferences unsubscribe.php jobalert job-listings ni ng emailers '
                          'track.php trackurl.php trackurl tracking.php logincontroller.php '
                          'nlogin login.php click.php mailerservice.php '.split())
SAFE_SEGMENTS = SAFE_SEGMENTS | frozenset(
    'user mnj settings career-tips career-advice blog search jobsearch jobs-search'.split())
SAFE_SUBDOMAINS = frozenset({'www', 'ae', 'm', 'email', 'click', 'tracking', 'links', 'jobs', 'job-boards', 'boards'})
ATS_HOSTS = {'boards.greenhouse.io', 'job-boards.greenhouse.io', 'jobs.ashbyhq.com'}
APP_STORE_HOSTS = {'play.google.com', 'apps.apple.com', 'itunes.apple.com'}
SOCIAL_HOSTS = {'twitter.com', 'www.twitter.com', 'www.facebook.com', 'facebook.com',
                'www.instagram.com', 'instagram.com'}


def url_shape(url, source, recognized=False):
    """Return only coarse hostname, fixed path template, and reason/category."""
    try:
        parts = urlsplit(url if isinstance(url, str) else '')
        host = (parts.hostname or '').lower()
        if not re.fullmatch(r'[a-z0-9.-]{1,253}', host):
            host = '<invalid-host>'
        labels = host.split('.')
        if len(labels) > 2 and host not in ATS_HOSTS:
            host = '.'.join(label if label in SAFE_SUBDOMAINS else '<subdomain>'
                            for label in labels[:-2]) + '.' + '.'.join(labels[-2:])
        segments = []
        for segment in parts.path.split('/'):
            if not segment:
                continue
            lower = segment.lower()
            if lower in SAFE_SEGMENTS:
                segments.append(lower)
            elif re.search(r'(?:-jid-|-id-)\d+', lower):
                segments.append('<job-slug>-jid-<id>')
            elif lower.startswith('job-listings-'):
                segments.append('job-listings-<slug>')
            elif re.fullmatch(r'\d+', lower):
                segments.append('<id>')
            elif len(segment) >= 32 and re.fullmatch(r'[A-Za-z0-9_=+%-]+', segment):
                segments.append('<opaque-segment>')
            elif re.fullmatch(r'[a-z0-9-]+\.php', lower):
                segments.append('<page>.php')
            else:
                segments.append('<segment>')
        pattern = '/' + '/'.join(segments[:8]) + ('/<more>' if len(segments) > 8 else '')
        category = 'selected_platform' if platform_domain((parts.hostname or '').lower(), source) else 'unverified_external'
        if (parts.hostname or '').lower() in ATS_HOSTS:
            category = 'approved_ats_host'
        elif (parts.hostname or '').lower() in APP_STORE_HOSTS:
            category = 'app_store'
        elif (parts.hostname or '').lower() in SOCIAL_HOSTS:
            category = 'social_site'
        if recognized:
            reason = 'recognized_job'
        elif parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
            reason = 'unsafe_url'
        elif category == 'approved_ats_host':
            reason = 'ats_route_not_supported'
        elif category in ('unverified_external', 'app_store', 'social_site'):
            reason = 'external_host_not_approved'
        else:
            query = parse_qs(parts.query, max_num_fields=64)
            explicit = any(key.lower() in {'url', 'u', 'r', 'dest', 'destination', 'redirect',
                                           'redirect_url', 'redirecturl', 'target', 'joburl', 'job_url'} for key in query)
            route_kind = naukri_route_kind(parts.path) if source == 'naukrigulf' else None
            if explicit:
                reason = 'embedded_destination_not_recognized'
            elif route_kind:
                reason = route_kind
            elif source == 'indeed' and parts.path.rstrip('/') in ('/rc/clk', '/rc/clk/dl', '/pagead/clk', '/viewjob'):
                valid_id = re.fullmatch(r'[A-Za-z0-9]{6,64}', (query.get('jk') or [''])[0])
                reason = 'supported_job_id_with_unsupported_route' if valid_id else 'job_route_without_supported_job_id'
            elif source == 'naukrigulf' and re.search(r'job|vacan', parts.path, re.I):
                reason = 'job_like_route_without_supported_job_id'
            elif source == 'naukrigulf' and any(re.fullmatch(r'\d{8,20}', value)
                                               for key, values in query.items()
                                               if key.lower() in ('jid', 'jobid', 'job_id') for value in values):
                reason = 'unsupported_route_with_explicit_job_id'
            elif any(len(segment) >= 32 and re.fullmatch(r'[A-Za-z0-9_=+%-]+', segment)
                     for segment in parts.path.split('/')):
                reason = 'opaque_path_without_explicit_job_destination'
            elif re.search(r'track|click|redirect|alert|email|/rc/', parts.path, re.I):
                reason = 'opaque_or_non_job_tracking_route'
            else:
                reason = 'non_job_or_unknown_route'
        return source, host, pattern, category, reason
    except (ValueError, TypeError):
        return source, '<invalid-host>', '/<invalid-path>', 'invalid', 'unsafe_url'


def naukri_route_kind(path):
    """Classify only unambiguous static route families, never a segment count."""
    segments = [part.lower() for part in path.split('/') if part]
    if segments and segments[0] in ('career-tips', 'career-advice', 'blog'):
        return 'editorial_or_career_advice_route'
    if segments and segments[0] in ('search', 'jobsearch', 'jobs-search'):
        return 'search_results_route'
    if any(part in ('autologin', 'auto-login', 'sso', 'authenticate', 'authentication') for part in segments):
        return 'authentication_gateway_not_job_detail'
    if 'settings' in segments and ('mailer' in segments or 'mailers' in segments):
        return 'mailer_settings_not_job_detail'
    if any(part in ('login', 'register', 'profile', 'preferences', 'unsubscribe') for part in segments):
        return 'account_or_preferences_route'
    if len(segments) == 3 and segments[1] == 'user':
        return 'user_service_route_without_verified_job_identity'
    if 'mailer' in segments or 'mailers' in segments:
        return 'mailer_route_not_job_detail'
    return None


def embedded_shapes(url, source, depth=0):
    """Inspect explicit destination structure only; no parameter names or values."""
    if depth > 3:
        return
    try:
        query = parse_qs(urlsplit(url).query, max_num_fields=64)
        for key, values in query.items():
            if key.lower() not in {'url', 'u', 'r', 'dest', 'destination', 'redirect',
                                    'redirect_url', 'redirecturl', 'target', 'joburl', 'job_url'}:
                continue
            for value in values:
                for _ in range(2):
                    if value.startswith(('https://', 'http://', '/')):
                        break
                    value = unquote(value)
                if value.startswith(('https://', 'http://')):
                    yield url_shape(value, source)
                    yield from embedded_shapes(value, source, depth + 1)
                elif value.startswith('/'):
                    shape = url_shape('https://' + (urlsplit(url).hostname or '') + value, source)
                    yield (*shape[:4], 'embedded_relative_path')
                else:
                    yield source, '<not-a-url>', '/<opaque>', 'embedded', 'no_explicit_destination'
    except (ValueError, TypeError):
        return
