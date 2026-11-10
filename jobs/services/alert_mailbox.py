"""Explicitly authorized, read-only TLS IMAP. Never prints server responses."""
import imaplib
import json
import re
import ssl
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses

from django.utils import timezone


PLATFORM_DOMAINS = {
    'linkedin': ('linkedin.com',), 'indeed': ('indeed.com', 'indeedemail.com'),
    'naukrigulf': ('naukrigulf.com',), 'bayt': ('bayt.com',),
}
MAX_BYTES = 2_000_000
ALERT_SUBJECT = re.compile(
    r'\b(?:jobs?|opportunities|vacancies|hiring|recommended|python|django|backend|'
    r'full[ -]?stack|software engineer|ai developer)\b', re.I,
)


class MailboxError(ValueError):
    pass


def platform_domain(domain, platform):
    return any(domain == base or domain.endswith('.' + base) for base in PLATFORM_DOMAINS[platform])


def load_mailbox_config(path):
    try:
        with open(path, encoding='utf-8') as handle:
            config = json.load(handle)
        if not isinstance(config, dict) or config.get('authorized') is not True:
            raise ValueError
        for key in ('username', 'host', 'folder', 'credential_service', 'trusted_authserv_id'):
            if not isinstance(config.get(key), str) or not config[key].strip() or re.search(r'[\r\n\x00]', config[key]):
                raise ValueError
        # Hostnames only, fixed implicit TLS port; no embedded URL or credentials.
        if not re.fullmatch(r'[A-Za-z0-9.-]+', config['host']):
            raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9.-]+', config['trusted_authserv_id']):
            raise ValueError
        for key, maximum in (('lookback_days', 90), ('max_messages', 1000)):
            value = config.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError
        senders = config['senders']
        if not isinstance(senders, dict) or not senders or set(senders) - set(PLATFORM_DOMAINS):
            raise ValueError
        for platform, addresses in senders.items():
            if not isinstance(addresses, list):
                raise ValueError
            for address in addresses:
                if (not isinstance(address, str) or not re.fullmatch(r'[a-zA-Z0-9_.+%-]+@[a-zA-Z0-9.-]+', address)
                        or not platform_domain(address.rsplit('@', 1)[-1].lower(), platform)):
                    raise ValueError
        if not any(senders.values()):
            raise ValueError
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise MailboxError('Mailbox configuration invalid or not explicitly authorized; see EMAIL_ALERTS.md') from None


def identify_source(message, config):
    addresses = getaddresses(message.get_all('From', []))
    if len(addresses) != 1:
        return None
    address = addresses[0][1].lower()
    platform = next((name for name, allowed in config['senders'].items()
                     if address in [value.lower() for value in allowed]), None)
    if platform is None:
        return None
    if not ALERT_SUBJECT.search(str(message.get('Subject', ''))):
        return None
    # Trust only the configured receiving provider's Authentication-Results.
    for result in message.get_all('Authentication-Results', []):
        if result.split(';', 1)[0].strip().lower() != config['trusted_authserv_id'].lower():
            continue
        dmarc = re.search(r'\bdmarc=pass\b[^;]*\bheader\.from=([^\s;()]+)', result, re.I)
        if dmarc and platform_domain(dmarc.group(1).lower(), platform):
            return platform
    return None


def secure_keyring():
    import keyring
    if type(keyring.get_keyring()).__module__ != 'keyring.backends.Windows':
        raise MailboxError('Windows Credential Manager backend required; plaintext credential stores refused')
    return keyring


def iter_alert_messages(config, counts, *, password=None, client_factory=imaplib.IMAP4_SSL):
    """Fetch headers first; retrieve selected bodies using PEEK in EXAMINE mode."""
    client = None
    try:
        if password is None:
            keyring = secure_keyring()
            password = keyring.get_password(config['credential_service'], config['username'])
        if not password:
            raise MailboxError('No mailbox password in OS credential store; run setup in EMAIL_ALERTS.md')
        # Prevent imaplib debug output from logging authentication material.
        imaplib.Debug = 0
        client = client_factory(config['host'], 993, ssl_context=ssl.create_default_context(), timeout=20)
        client.debug = 0
        status, _ = client.login(config['username'], password)
        if status != 'OK':
            raise MailboxError('Mailbox authentication failed')
        quoted_folder = '"' + config['folder'].replace('\\', '\\\\').replace('"', '\\"') + '"'
        status, _ = client.select(quoted_folder, readonly=True)
        if status != 'OK':
            raise MailboxError('Authorized job-alert folder cannot be opened')
        since = (timezone.now() - timedelta(days=config['lookback_days'])).strftime('%d-%b-%Y')
        uids = set()
        for addresses in config['senders'].values():
            for address in addresses:
                status, data = client.uid('search', None, 'SINCE', since, 'FROM', '"' + address + '"')
                if status != 'OK':
                    raise MailboxError('Mailbox alert search failed')
                uids.update((data[0] or b'').split())
        ordered = sorted(uids, key=int, reverse=True)
        counts['messages_outside_run_limit'] += max(0, len(ordered) - config['max_messages'])
        for uid in ordered[:config['max_messages']]:
            status, data = client.uid('fetch', uid, '(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT AUTHENTICATION-RESULTS)])')
            if status != 'OK':
                raise MailboxError('Mailbox header fetch failed')
            literals = [item for item in data if isinstance(item, tuple)]
            if len(literals) != 1:
                counts['invalid_headers'] += 1
                continue
            metadata, header = literals[0]
            size = re.search(rb'RFC822.SIZE (\d+)', metadata)
            if not size or int(size.group(1)) > MAX_BYTES:
                counts['oversized_or_unknown_size'] += 1
                continue
            message = BytesParser(policy=policy.default).parsebytes(header)
            source = identify_source(message, config)
            if source is None:
                counts['untrusted_or_unselected_sender'] += 1
                continue
            status, data = client.uid('fetch', uid, '(BODY.PEEK[])')
            if status != 'OK':
                raise MailboxError('Mailbox body fetch failed')
            bodies = [item[1] for item in data if isinstance(item, tuple)]
            if len(bodies) != 1 or len(bodies[0]) > MAX_BYTES:
                counts['invalid_or_oversized_body'] += 1
                continue
            raw = bodies[0]
            full = BytesParser(policy=policy.default).parsebytes(raw)
            if identify_source(full, config) != source:
                counts['untrusted_or_unselected_sender'] += 1
                continue
            counts['alert_messages_read'] += 1
            yield source, full
    except MailboxError:
        raise
    except Exception:
        # Server responses and keyring exceptions may contain private details.
        raise MailboxError('Mailbox connection, credential-store, or protocol failure; details suppressed') from None
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
