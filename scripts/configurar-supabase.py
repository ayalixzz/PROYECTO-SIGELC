"""Prepara el esquema y rol exclusivos para la demo de SIGELC en Supabase."""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import subprocess
import sys

import psycopg
from psycopg import sql


BACKEND = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND))

from config.postgres_supabase import DATABASE_NAME, DATABASE_ROLE, SCHEMA, pooler_project


def provisionar(host, proyecto, certificado, admin_password, archivo):
    rol = sql.Identifier(DATABASE_ROLE)
    esquema = sql.Identifier(SCHEMA)
    password = token_urlsafe(42)
    opciones = {
        'host': host,
        'port': 5432,
        'dbname': DATABASE_NAME,
        'sslmode': 'verify-full',
        'sslrootcert': str(certificado),
        'connect_timeout': 8,
    }
    creado = False
    try:
        with psycopg.connect(user=f'postgres.{proyecto}', password=admin_password, **opciones) as conexion:
            with conexion.cursor() as cursor:
                cursor.execute('SELECT current_database(), current_user')
                if cursor.fetchone() != (DATABASE_NAME, 'postgres'):
                    raise ValueError('Supabase no confirmó la base postgres y el rol de configuración.')
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('sigelc_demo.configurar_supabase'))")
                cursor.execute(
                    'SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = %s) '
                    'OR EXISTS (SELECT FROM pg_namespace WHERE nspname = %s)',
                    (DATABASE_ROLE, SCHEMA),
                )
                if cursor.fetchone()[0]:
                    raise ValueError('El rol o esquema de SIGELC ya existe. Se conservó la configuración remota.')
                verificador = conexion.pgconn.encrypt_password(
                    password.encode(), DATABASE_ROLE.encode(), b'scram-sha-256',
                ).decode('ascii')
                cursor.execute(sql.SQL(
                    'CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}'
                ).format(rol, sql.Literal(verificador)))
                cursor.execute(sql.SQL('CREATE SCHEMA {}').format(esquema))
                cursor.execute(sql.SQL(
                    'REVOKE ALL ON SCHEMA {} FROM PUBLIC, anon, authenticated, service_role'
                ).format(esquema))
                cursor.execute(sql.SQL('GRANT USAGE, CREATE ON SCHEMA {} TO {}').format(esquema, rol))

            variables = {
                'SIGELC_SUPABASE_HOST': host,
                'SIGELC_SUPABASE_USER': f'{DATABASE_ROLE}.{proyecto}',
                'SIGELC_SUPABASE_PASSWORD': password,
                'SIGELC_SUPABASE_CA': str(certificado),
                'SIGELC_SUPABASE_SECRET_KEY': token_urlsafe(48),
            }
            fd = os.open(archivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            creado = True
            with os.fdopen(fd, 'w', encoding='utf-8') as salida:
                for nombre, valor in variables.items():
                    salida.write(f'{nombre}={json.dumps(valor, ensure_ascii=False)}\n')
    except Exception:
        if creado:
            archivo.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--proyecto', required=True, help='Referencia del proyecto en el usuario del Session pooler.')
    parser.add_argument('--certificado', type=Path, help='Certificado CA descargado desde el panel de Supabase.')
    args = parser.parse_args()
    archivo = BACKEND / '.env.supabase'
    try:
        if archivo.exists():
            raise ValueError('Ya existe backend/.env.supabase. Se conserva la configuración local.')
        pooler_project(args.host, f'postgres.{args.proyecto}', role='postgres')
        if not sys.stdin.isatty():
            raise ValueError('Ejecute el asistente en una terminal interactiva para introducir la contraseña.')
        certificado = args.certificado or Path(input('Ruta del certificado CA de Supabase: ').strip().strip('"'))
        certificado = certificado.resolve(strict=True)
        if not certificado.is_file():
            raise ValueError('La ruta del certificado debe apuntar a un archivo.')
        admin_password = getpass('Contraseña de la base de Supabase: ')
        if not admin_password:
            raise ValueError('La contraseña es obligatoria.')
        provisionar(args.host, args.proyecto, certificado, admin_password, archivo)
    except ValueError as exc:
        parser.exit(1, f'{exc}\n')
    except psycopg.OperationalError as exc:
        error = str(exc).lower()
        if 'certificate' in error or 'ssl' in error:
            motivo = 'Revisa el certificado CA descargado de Supabase.'
        elif 'password' in error or 'authentication' in error:
            motivo = 'Revisa la contraseña de la base y el proyecto.'
        else:
            motivo = 'Revisa el host del Session pooler, el acceso al puerto 5432 y el estado del proyecto.'
        parser.exit(1, f'No se pudo preparar la conexión a Supabase. {motivo}\n')
    except psycopg.Error as exc:
        parser.exit(1, f'PostgreSQL rechazó la operación (SQLSTATE {exc.sqlstate or "desconocido"}).\n')
    except OSError:
        parser.exit(1, 'No se pudo leer el certificado o escribir la configuración local.\n')

    comando = [sys.executable, 'manage.py', 'shell', '--settings=config.settings_supabase', '-c']
    consulta = (
        "from django.db import connection; "
        "connection.ensure_connection(); "
        "print('Conexión de Django verificada con el esquema sigelc_demo.')"
    )
    comprobacion = subprocess.run([*comando, consulta], cwd=BACKEND, stdout=subprocess.PIPE)
    sys.stdout.buffer.write(comprobacion.stdout)
    if comprobacion.returncode:
        parser.exit(1, 'El esquema y el rol quedaron configurados. Falta validar la conexión de Django.\n')


if __name__ == '__main__':
    main()
