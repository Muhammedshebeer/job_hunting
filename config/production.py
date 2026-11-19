"""Render dashboard only. No migrations, workers, mailbox or report delivery."""
from .settings import *  # noqa: F403
from django.core.exceptions import ImproperlyConfigured
from config.database import database_from_url


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} is required in production.')
    return value


DEBUG = False
DASHBOARD_READ_ONLY = True  # Deliberately cannot be disabled by a Render env var.
SECRET_KEY = required('DJANGO_SECRET_KEY')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith('django-insecure-'):
    raise ImproperlyConfigured('Use a randomly generated production DJANGO_SECRET_KEY of at least 50 characters.')
ALLOWED_HOSTS = [v.strip() for v in required('DJANGO_ALLOWED_HOSTS').split(',') if v.strip()]
if any('*' in host or '://' in host or '/' in host for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS must contain exact hostnames, without schemes or wildcards.')
CSRF_TRUSTED_ORIGINS = [v.strip() for v in required('DJANGO_CSRF_TRUSTED_ORIGINS').split(',') if v.strip()]
if any(not origin.startswith('https://') or '*' in origin for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured('Use exact HTTPS origins for DJANGO_CSRF_TRUSTED_ORIGINS.')
DATABASES = {'default': database_from_url(required('DATABASE_URL'))}
DATABASES['default']['OPTIONS']['options'] = '-c default_transaction_read_only=on'
MIDDLEWARE = [MIDDLEWARE[0], 'whitenoise.middleware.WhiteNoiseMiddleware',
    'config.middleware.ReadOnlyDashboardMiddleware', *MIDDLEWARE[1:]]
TEMPLATES[0]['OPTIONS']['context_processors'].append('config.context_processors.deployment_mode')
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_NAME = 'uae_dashboard_session'
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r'^healthz/$']
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
EMAIL_BACKEND = REPORT_EMAIL_BACKEND = 'config.email_backend.DisabledEmailBackend'
