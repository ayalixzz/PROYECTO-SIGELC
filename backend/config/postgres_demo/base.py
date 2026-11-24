from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper
from django.db.backends.postgresql.creation import DatabaseCreation as PostgreSQLCreation

from . import DATABASE_NAME, DATABASE_OID, DATABASE_ROLE, LOCAL_DATABASE_HOSTS


class DatabaseCreation(PostgreSQLCreation):
    def create_test_db(self, *args, **kwargs):
        raise ImproperlyConfigured("La base de la demo debe existir. Use el ejecutor aislado de SIGELC.")

    def destroy_test_db(self, *args, **kwargs):
        raise ImproperlyConfigured("La base persistente de la demo no se puede eliminar con Django.")


class DatabaseWrapper(PostgreSQLWrapper):
    creation_class = DatabaseCreation

    def get_new_connection(self, conn_params):
        target = (conn_params.get("dbname"), conn_params.get("user"), str(conn_params.get("port")))
        if (
            self.alias != "default"
            or target != (DATABASE_NAME, DATABASE_ROLE, "5432")
            or conn_params.get("host") not in LOCAL_DATABASE_HOSTS
            or set(self.settings_dict["OPTIONS"]) != {"connect_timeout"}
        ):
            raise ImproperlyConfigured("Destino PostgreSQL fuera del entorno aislado de SIGELC.")

        raw_connection = super().get_new_connection(conn_params)
        try:
            with raw_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(), d.oid, host(inet_server_addr()), "
                    "inet_server_port(), current_user, r.rolsuper, r.rolcreatedb "
                    "FROM pg_database d JOIN pg_roles r ON r.rolname = current_user "
                    "WHERE d.datname = current_database()"
                )
                actual = cursor.fetchone()
            if not actual or actual[:2] != (DATABASE_NAME, DATABASE_OID) or (
                actual[2] not in LOCAL_DATABASE_HOSTS - {"localhost"}
                or actual[3:] != (5432, DATABASE_ROLE, False, False)
            ):
                raise ImproperlyConfigured(
                    "Django no pudo confirmar el nombre, OID, servidor o rol de la base aislada."
                )
            raw_connection.rollback()  # La comprobación no deja abierta una transacción.
        except Exception:
            raw_connection.close()
            raise
        return raw_connection
