"""Shared hosted PostgreSQL configuration; never include URLs in errors."""
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured


def database_from_url(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ('postgres', 'postgresql') or not url.hostname or not url.path.strip('/') or url.fragment:
            raise ValueError
        options = dict(parse_qsl(url.query, strict_parsing=True))
        if set(options) - {'sslmode', 'sslrootcert', 'channel_binding', 'connect_timeout'}:
            raise ValueError
        if options.get('sslmode', 'require') not in ('require', 'verify-ca', 'verify-full'):
            raise ValueError
        options.setdefault('sslmode', 'require')
        options.setdefault('connect_timeout', 15)
        return {'ENGINE': 'django.db.backends.postgresql',
            'NAME': unquote(url.path.lstrip('/')), 'USER': unquote(url.username or ''),
            'PASSWORD': unquote(url.password or ''), 'HOST': url.hostname,
            'PORT': str(url.port or 5432), 'OPTIONS': options,
            'CONN_MAX_AGE': 0, 'CONN_HEALTH_CHECKS': True}
    except (ValueError, TypeError):
        raise ImproperlyConfigured('DATABASE_URL must be a PostgreSQL URL with SSL enabled and supported connection options.') from None
