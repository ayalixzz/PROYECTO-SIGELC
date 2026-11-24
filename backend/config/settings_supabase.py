"""Perfil de revisión de SIGELC con PostgreSQL de Supabase."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values

from .postgres_supabase import DATABASE_NAME, DATABASE_ROLE, pooler_project


_backend = Path(__file__).resolve().parent.parent
_config = dotenv_values(_backend / '.env.supabase', interpolate=False)


def _require(name):
    value = os.environ.get(name, _config.get(name))
    if not value:
        raise ImproperlyConfigured(f'Configure {name} en el entorno o en backend/.env.supabase.')
    return value


_secret_key = _require('SIGELC_SUPABASE_SECRET_KEY')
os.environ.setdefault('DJANGO_SECRET_KEY', _secret_key)

from .settings import *  # noqa: E402,F403

_host = _require('SIGELC_SUPABASE_HOST')
_user = _require('SIGELC_SUPABASE_USER')
_password = _require('SIGELC_SUPABASE_PASSWORD')
_certificate = Path(_require('SIGELC_SUPABASE_CA')).resolve()
try:
    pooler_project(_host, _user, role=DATABASE_ROLE)
except ValueError as exc:
    raise ImproperlyConfigured(str(exc)) from exc
if not _certificate.is_file():
    raise ImproperlyConfigured('No se encuentra el certificado CA de Supabase.')

SECRET_KEY = _secret_key
DEBUG = False
SIGELC_DEMO = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
CORS_ALLOWED_ORIGINS = []
CSRF_TRUSTED_ORIGINS = []
SESSION_COOKIE_NAME = 'sigelc_supabase_session'
CSRF_COOKIE_NAME = 'sigelc_supabase_csrf'
SIGELC_CLAUDE_ENTREGAS = ()
SIGELC_IA_PROVEEDOR = ''
SIGELC_IA_ENVIO_FICTICIO = False
STATIC_ROOT = _backend / 'staticfiles_supabase'
STORAGES = {
    'default': {'BACKEND': 'gestion_contractual.archivos_postgresql.AlmacenamientoPrivadoPostgreSQL'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
DATABASES = {
    'default': {
        'ENGINE': 'config.postgres_supabase',
        'NAME': DATABASE_NAME,
        'HOST': _host,
        'PORT': '5432',
        'USER': _user,
        'PASSWORD': _password,
        'OPTIONS': {'sslmode': 'verify-full', 'sslrootcert': str(_certificate), 'connect_timeout': 8},
        'CONN_MAX_AGE': 0,
        'DISABLE_SERVER_SIDE_CURSORS': True,
    }
}
