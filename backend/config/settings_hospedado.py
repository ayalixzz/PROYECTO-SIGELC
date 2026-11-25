"""Configuración de revisión para el servicio HTTPS administrado por Render."""

import os
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


_hostname = os.getenv('RENDER_EXTERNAL_HOSTNAME', '')
if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com', _hostname):
    raise ImproperlyConfigured('Se requiere el dominio exacto del servicio Render.')

_ca = Path(__file__).with_name('certificados') / 'supabase-prod-ca-2021.crt'
os.environ.setdefault('SIGELC_SUPABASE_CA', str(_ca))

from .settings_supabase import *  # noqa: E402,F403

ALLOWED_HOSTS = [_hostname]
_entregas_mcp = os.getenv('SIGELC_MCP_ENTREGAS', '')
if _entregas_mcp and not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17})?', _entregas_mcp):
    raise ImproperlyConfigured('SIGELC_MCP_ENTREGAS debe identificar hasta dos entregas ficticias.')
SIGELC_MCP_ENTREGAS = tuple(map(int, _entregas_mcp.split(','))) if _entregas_mcp else ()
SIGELC_MCP_PROPUESTAS = bool(SIGELC_MCP_ENTREGAS) and os.getenv('SIGELC_MCP_PROPUESTAS') == 'si'

CSRF_TRUSTED_ORIGINS = [f'https://{_hostname}']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

FRONTEND_DIST = BASE_DIR.parent / 'frontend' / 'dist'
if not (FRONTEND_DIST / 'index.html').is_file():
    raise ImproperlyConfigured('La imagen debe incluir el frontend compilado.')

STATIC_URL = '/assets/'
STATIC_ROOT = FRONTEND_DIST / 'assets'
WHITENOISE_ROOT = FRONTEND_DIST
WHITENOISE_INDEX_FILE = True
WHITENOISE_ALLOW_ALL_ORIGINS = False
WHITENOISE_MAX_AGE = 0
WHITENOISE_IMMUTABLE_FILE_TEST = r'^/assets/[^/]+-[A-Za-z0-9_-]{8}\.[^/]+$'
# Reuse Django's Host check for the frontend and static assets.
_after_host_check = MIDDLEWARE.index('django.middleware.common.CommonMiddleware') + 1
MIDDLEWARE = [
    *MIDDLEWARE[:_after_host_check],
    'whitenoise.middleware.WhiteNoiseMiddleware',
    *MIDDLEWARE[_after_host_check:],
]
