import os

from django.core.exceptions import ImproperlyConfigured


DATABASE_NAME = "test_sigelc_db"
_oid = os.environ.get("SIGELC_DEMO_DB_OID", "")
if not (1 <= len(_oid) <= 10 and _oid.isascii() and _oid.isdecimal() and 0 < int(_oid) < 2**32):
    raise ImproperlyConfigured("Configure SIGELC_DEMO_DB_OID con el OID local de test_sigelc_db.")
DATABASE_OID = int(_oid)
DATABASE_ROLE = "sigelc_user"
LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}
