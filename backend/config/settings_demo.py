"""Local SIGELC demo. The PostgreSQL backend checks each physical connection."""

from .settings import *  # noqa: F403
from . import settings as base
from .postgres_demo import DATABASE_NAME, DATABASE_ROLE

SIGELC_DEMO = True
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
CORS_ALLOWED_ORIGINS = []
CSRF_TRUSTED_ORIGINS = []
SESSION_COOKIE_NAME = "sigelc_demo_session"
CSRF_COOKIE_NAME = "sigelc_demo_csrf"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

DATABASES = {
    "default": {
        "ENGINE": "config.postgres_demo",
        "NAME": DATABASE_NAME,
        "USER": DATABASE_ROLE,
        "PASSWORD": base.DATABASES["default"]["PASSWORD"],
        "HOST": "localhost",
        "PORT": "5432",
        "OPTIONS": {"connect_timeout": 5},
        "TEST": {"NAME": DATABASE_NAME},
    }
}
DATABASE_ROUTERS = []
MEDIA_ROOT = base.BASE_DIR / "private_media_demo"
STATIC_ROOT = base.BASE_DIR / "staticfiles_demo"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
TEST_RUNNER = "config.demo_test_runner.IsolatedDemoRunner"

# There is no network provider at startup unless explicitly enabled for the demo.
SIGELC_IA_PROVEEDOR = base.os.getenv("SIGELC_IA_PROVEEDOR", "").strip().lower()
SIGELC_IA_ENVIO_FICTICIO = base.os.getenv("SIGELC_IA_ENVIO_FICTICIO", "") == "si"

# Explicit source allowlist for the Windows demo. Disabled by default.
SIGELC_CLAUDE_CONSULTA_LOCAL = True
_claude = base.os.getenv("SIGELC_CLAUDE_ENTREGAS", "").split(",")
SIGELC_CLAUDE_ENTREGAS = tuple(int(valor) for valor in _claude
                              if 1 <= len(valor) <= 19 and valor.isascii() and valor.isdecimal()
                              and 0 < int(valor) <= 2**63 - 1)
if len(SIGELC_CLAUDE_ENTREGAS) != len(_claude) or len(set(SIGELC_CLAUDE_ENTREGAS)) != len(_claude) or len(_claude) > 4:
    SIGELC_CLAUDE_ENTREGAS = ()
