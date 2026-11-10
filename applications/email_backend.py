"""Private SMTP configuration; password resolved only when a worker sends mail."""
import json
import re
from pathlib import Path
from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend
from jobs.services.alert_mailbox import secure_keyring

REPORT_RECIPIENT = 'shabeersh122@gmail.com'


class DeliveryConfigurationError(ValueError):
    pass


def load_delivery_config():
    path = getattr(settings, 'REPORT_EMAIL_CONFIG', Path(__file__).resolve().parents[1] / 'email_delivery.local.json')
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'))
        allowed = {'authorized', 'username', 'from_email', 'recipient', 'host', 'port', 'use_tls', 'use_ssl', 'credential_service'}
        if not isinstance(config, dict) or set(config) != allowed or config['authorized'] is not True:
            raise ValueError
        for key in ('username', 'from_email', 'credential_service', 'host'):
            if not isinstance(config[key], str) or not config[key].strip() or re.search(r'[\r\n\x00]', config[key]):
                raise ValueError
        if config['recipient'] != REPORT_RECIPIENT:
            raise ValueError
        from django.core.validators import validate_email
        validate_email(config['username'])
        validate_email(config['from_email'])
        if not re.fullmatch(r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', config['host']):
            raise ValueError
        if type(config['use_tls']) is not bool or type(config['use_ssl']) is not bool:
            raise ValueError
        if not ((config['port'] == 587 and config['use_tls'] and not config['use_ssl']) or
                (config['port'] == 465 and config['use_ssl'] and not config['use_tls'])):
            raise ValueError
        return config
    except Exception:
        raise DeliveryConfigurationError('Email delivery is not securely configured and explicitly authorized; see DAILY_AUTOMATION.md.') from None


class CredentialSMTPBackend(EmailBackend):
    def __init__(self, **kwargs):
        config = load_delivery_config()
        try:
            password = secure_keyring().get_password(config['credential_service'], config['username'])
            if not password:
                raise ValueError
        except Exception:
            raise DeliveryConfigurationError('No SMTP password in Windows Credential Manager. Run configure_report_email.') from None
        super().__init__(host=config['host'], port=config['port'], username=config['username'], password=password,
            use_tls=config['use_tls'], use_ssl=config['use_ssl'], timeout=30, fail_silently=False)
