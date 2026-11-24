"""Claude Desktop entry point. Standard output belongs exclusively to MCP."""

import argparse
from contextlib import redirect_stdout
from getpass import getpass
import json
import os
from pathlib import Path
import sys

with redirect_stdout(sys.stderr):
    import django

    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_demo'
    django.setup()

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import close_old_connections

from gestion_contractual import mcp_demo as servicio
from gestion_contractual.mcp_tools import Lote, Propuesta, crear_mcp  # Propuesta forma parte de la API local.

mcp = crear_mcp(acceso=lambda herramienta: servicio.acceso(), escritura=True)


def autenticar():
    usuario = authenticate(username=input('Usuario SIGELC de la demo: ').strip(), password=getpass())
    if usuario is None:
        raise ValidationError('Credenciales incorrectas.')
    return usuario


def autorizar(args):
    servicio.verificar_base()
    usuario = autenticar()
    contrato = servicio.expediente_ficticio(usuario)
    if args.aprobar_operacion:
        autorizado, _, permiso = servicio.acceso()
        if autorizado.pk != usuario.pk or not permiso['escritura']:
            raise ValidationError('Se requiere una autorización vigente de propuestas del mismo abogado.')
        with Path(args.aprobar_operacion).open('rb') as archivo:
            contenido = archivo.read(48001)
        if len(contenido) > 48000:
            raise ValidationError('El lote excede el límite de 48 KB.')
        lote = Lote.model_validate_json(contenido.decode('utf-8-sig'))
        if lote.contrato_id != contrato.pk:
            raise ValidationError('Expediente no autorizado.')
        revision = servicio.entrega(contrato, permiso, lote.entrega_id)
        servicio.validar_propuestas(revision, lote.operacion, lote.propuestas)
        print(json.dumps(lote.model_dump(mode='json'), indent=2, ensure_ascii=False))
        if input('Tras revisar el lote mostrado, escriba APROBAR: ') != 'APROBAR':
            raise ValidationError('Operación sin confirmar.')
        permiso['aprobacion'] = [str(lote.operacion), servicio.firma_propuesta(revision, lote.operacion, lote.propuestas)]
    else:
        disponibles = dict(contrato.revisiones_inteligentes.values_list('pk', 'version'))
        print('Entregas disponibles (ID, versión):', list(disponibles.items()))
        ids = sorted(set(int(i) for i in input('IDs de entregas ficticias, separados por espacio: ').split()))
        if not ids or len(ids) > 4 or not set(ids) <= disponibles.keys():
            raise ValidationError('Seleccione hasta cuatro entregas del expediente.')
        if args.escritura and not usuario.has_perm('gestion_contractual.change_documentocontrato'):
            raise ValidationError('La cuenta carece de permiso para propuestas.')
        if input('Si confirma que las entregas son ficticias y autoriza su uso en Claude, escriba AUTORIZAR: ') != 'AUTORIZAR':
            raise ValidationError('Autorización cancelada.')
        permiso = {'usuario': usuario.pk, 'autenticacion': usuario.get_session_auth_hash(),
                   'contrato': contrato.pk, 'version_contrato': contrato.version_registro,
                   'entregas': ids, 'escritura': args.escritura}
    servicio.guardar_autorizacion(permiso)
    print('Autorización local actualizada.')


def main():
    parser = argparse.ArgumentParser(description='Conector SIGELC de datos ficticios')
    acciones = parser.add_mutually_exclusive_group()
    acciones.add_argument('--verificar', action='store_true')
    acciones.add_argument('--autorizar', action='store_true')
    acciones.add_argument('--revocar', action='store_true')
    acciones.add_argument('--aprobar-operacion', metavar='ARCHIVO_JSON')
    parser.add_argument('--escritura', action='store_true')
    args = parser.parse_args()
    try:
        if args.revocar:
            servicio.ARCHIVO_AUTORIZACION.unlink(missing_ok=True)
            print('Autorización local revocada.')
        elif args.autorizar or args.aprobar_operacion:
            autorizar(args)
        elif args.verificar:
            from config.postgres_demo import DATABASE_NAME, DATABASE_OID

            servicio.verificar_base()
            print(f'{DATABASE_NAME} · OID {DATABASE_OID} · conexión aislada verificada.')
        else:
            servicio.verificar_base()
            close_old_connections()
            mcp.run(transport='stdio')
    except (ValueError, ValidationError) as error:
        print(' '.join(error.messages) if isinstance(error, ValidationError) else 'Datos de operación inválidos.', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'No se pudo iniciar SIGELC MCP ({type(error).__name__}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
