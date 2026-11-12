"""Render settings for the actual read-only Django application."""
import os
from config.settings import *  # noqa: F403

DEBUG = False
SECRET_KEY = os.environ['DJANGO_SECRET_KEY']
hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip()
explicit = os.environ.get('ALLOWED_HOSTS', '').strip()
ALLOWED_HOSTS = [part.strip() for part in explicit.split(',') if part.strip()] if explicit else ([hostname] if hostname else [])
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise RuntimeError('Production requires an explicit ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME.')
os.environ['ALLOW_LIVE_SPARK'] = '0'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
MIDDLEWARE = [*MIDDLEWARE, 'django.middleware.clickjacking.XFrameOptionsMiddleware']  # noqa: F405
WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
