from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper
from django.db.backends.postgresql.creation import DatabaseCreation as PostgreSQLCreation

from . import DATABASE_NAME, DATABASE_ROLE, SCHEMA, pooler_project


class DatabaseCreation(PostgreSQLCreation):
    def create_test_db(self, *args, **kwargs):
        raise ImproperlyConfigured('Ejecute las pruebas en la base local aislada, no en Supabase.')

    def destroy_test_db(self, *args, **kwargs):
        raise ImproperlyConfigured('Django no puede eliminar la base de Supabase.')


class DatabaseWrapper(PostgreSQLWrapper):
    creation_class = DatabaseCreation

    def get_new_connection(self, conn_params):
        try:
            pooler_project(conn_params.get('host', ''), conn_params.get('user', ''), role=DATABASE_ROLE)
        except ValueError as exc:
            raise ImproperlyConfigured(str(exc)) from exc
        if (
            self.alias != 'default'
            or conn_params.get('dbname') != DATABASE_NAME
            or str(conn_params.get('port')) != '5432'
            or conn_params.get('sslmode') != 'verify-full'
            or not conn_params.get('sslrootcert')
        ):
            raise ImproperlyConfigured('La conexión debe usar postgres y TLS verificado en Supabase.')

        raw_connection = super().get_new_connection(conn_params)
        try:
            with raw_connection.cursor() as cursor:
                cursor.execute("SELECT set_config('search_path', %s, false)", (SCHEMA,))
                cursor.execute(
                    'SELECT current_database(), current_user, current_schema(), '
                    "has_schema_privilege(current_user, n.oid, 'CREATE'), "
                    'r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls, '
                    "has_schema_privilege('anon', n.oid, 'USAGE') OR "
                    "has_schema_privilege('authenticated', n.oid, 'USAGE') OR "
                    "has_schema_privilege('service_role', n.oid, 'USAGE') "
                    'FROM pg_roles r JOIN pg_namespace n ON n.nspname = current_schema() '
                    'WHERE r.rolname = current_user'
                )
                expected = (DATABASE_NAME, DATABASE_ROLE, SCHEMA, True, False, False)
                if cursor.fetchone() != expected:
                    raise ImproperlyConfigured('No se pudo verificar el rol y el esquema privado de SIGELC.')
            raw_connection.commit()
        except Exception:
            raw_connection.close()
            raise
        return raw_connection
