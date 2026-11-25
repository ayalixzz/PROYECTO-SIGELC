"""Carga de una demostración ficticia sobre la base PostgreSQL aislada."""

import json
import os
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from getpass import getpass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections
from django.utils import timezone

from gestion_contractual.archivos_transaccionales import escritura_con_archivo
from gestion_contractual.calendario import calendario_disponible
from gestion_contractual.calendarios_servicios import aprobar, proponer, vigente
from gestion_contractual.competencia import Estado, evaluar, sumar_meses
from gestion_contractual.contenido_demo import plantilla_ficticia
from gestion_contractual.models import (
    Auditoria, Contrato, DocumentoContrato, EntidadPublica, EstadoContrato,
    PerfilUsuario, PlantillaDocumento, TipoRequerimiento, TransicionEstado,
)
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.services import cambiar_estado, guardar_contrato, iniciar_seguimiento


NIT_DEMO = 'SIGELC-DEMO-000'
ENTIDAD_DEMO = 'Entidad pública ficticia · demostración SIGELC'
CARGO_DEMO = 'Abogado de demostración SIGELC'
DEPENDENCIA_DEMO = 'Equipo contractual ficticio'
SUFIJO_COMPETENCIA_DEMO = '006'
PRIMERO = TipoRequerimiento.PRIMERO
FLUJO_DEMO = (
    PRIMERO,
    'revision-abogado-1',
    'revision-supervisor-1',
    TipoRequerimiento.SEGUNDO,
    'revision-abogado-2',
    'revision-supervisor-2',
    TipoRequerimiento.TERCERO,
)
PLANTILLAS = {
    PRIMERO: 'v1',
    TipoRequerimiento.SEGUNDO: 'v2',
    TipoRequerimiento.TERCERO: 'v2',
}
PERMISOS_DEMO = (
    'view_contrato', 'add_contrato', 'change_contrato', 'view_documentocontrato',
    'add_documentocontrato', 'change_documentocontrato', 'view_entidadpublica',
    'view_estadocontrato', 'view_historialestadocontrato',
)


def clave_operacion(clave):
    return uuid5(NAMESPACE_URL, f'urn:sigelc:demo:v1:{clave}')


def verificar_aislamiento(modo_plantillas):
    datos = connection.settings_dict
    if settings.SETTINGS_MODULE == 'config.settings_hospedado':
        from gestion_contractual.archivos_postgresql import AlmacenamientoPrivadoPostgreSQL

        if modo_plantillas != 'ficticias':
            raise CommandError('La demo hospedada solo admite --plantillas ficticias.')
        if (list(connections) != ['default'] or settings.DATABASE_ROUTERS
                or datos['ENGINE'] != 'config.postgres_supabase'
                or not isinstance(default_storage, AlmacenamientoPrivadoPostgreSQL)):
            raise CommandError('La demostración hospedada requiere el esquema y almacenamiento privados.')
        connection.ensure_connection()
        if not connection.connection.pgconn.ssl_in_use:
            raise CommandError('La demostración hospedada requiere TLS.')
        return

    if settings.SETTINGS_MODULE != 'config.settings_demo':
        raise CommandError('Ejecute la carga con --settings=config.settings_demo o el perfil hospedado de SIGELC.')
    from config.postgres_demo import DATABASE_OID

    if (list(connections) != ['default'] or settings.DATABASE_ROUTERS
            or datos['ENGINE'] != 'config.postgres_demo' or connection.vendor != 'postgresql'
            or datos['NAME'] != 'test_sigelc_db' or datos['TEST']['NAME'] != 'test_sigelc_db'
            or datos['TEST'].get('MIRROR') or datos['HOST'] not in ('localhost', '127.0.0.1', '::1')
            or str(datos['PORT']) != '5432'):
        raise CommandError('La conexión configurada no cumple el aislamiento de la demostración.')
    media = Path(settings.BASE_DIR).resolve() / 'private_media_demo'
    if Path(settings.MEDIA_ROOT).resolve() != media or Path(default_storage.path('')).resolve() != media:
        raise CommandError('El almacenamiento de la demostración debe ser private_media_demo.')
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT current_database(), d.oid, host(inet_server_addr()), inet_server_port(), '
            'current_user, r.rolsuper, r.rolcreatedb '
            'FROM pg_database d CROSS JOIN pg_roles r '
            'WHERE d.datname = current_database() AND r.rolname = current_user'
        )
        db, oid, servidor, puerto, rol, superusuario, crea_db = cursor.fetchone()
    if ((db, oid, puerto, rol, superusuario, crea_db) != ('test_sigelc_db', DATABASE_OID, 5432, 'sigelc_user', False, False)
            or servidor not in ('127.0.0.1', '::1')):
        raise CommandError('La conexión efectiva de PostgreSQL no coincide con la demostración aislada.')


def leer_password(usuario, variable):
    if variable:
        if not re.fullmatch(r'SIGELC_DEMO_[A-Z0-9_]+', variable):
            raise CommandError('Utilice una variable de entorno con prefijo SIGELC_DEMO_ para la clave.')
        password = os.environ.get(variable)
        if not password:
            raise CommandError('La variable indicada no tiene una contraseña de demostración.')
    else:
        if not sys.stdin.isatty():
            raise CommandError('Abra una terminal interactiva o use --password-env con una variable local.')
        password = getpass('Contraseña del abogado de demostración: ')
        if password != getpass('Confirme la contraseña: '):
            raise CommandError('Las contraseñas no coinciden.')
    password_validation.validate_password(password, usuario)
    return password


def aprovisionar_abogado(username, variable, cambiar_clave):
    User = get_user_model()
    perfil = PerfilUsuario.objects.select_related('usuario').filter(abogado_operativo=True).first()
    usuario = User.objects.filter(username=username).first()
    if perfil and (perfil.usuario_id != getattr(usuario, 'pk', None)
                   or perfil.nombre_cargo != CARGO_DEMO or perfil.dependencia != DEPENDENCIA_DEMO):
        raise CommandError('La base contiene otro perfil operativo. Se conserva sin cambios.')
    es_nuevo = usuario is None
    if es_nuevo:
        usuario = User(username=username, is_staff=True, first_name='Abogado', last_name='Demo SIGELC')
        usuario.full_clean(exclude=['password'])
    elif (perfil is None or not perfil.activo or not usuario.is_active or not usuario.is_staff
          or usuario.is_superuser or usuario.groups.exists()):
        raise CommandError('El usuario existente no corresponde al abogado operativo de demostración.')
    if es_nuevo or cambiar_clave:
        usuario.set_password(leer_password(usuario, variable))
        if es_nuevo:
            usuario.save(force_insert=True)
            PerfilUsuario.objects.create(
                usuario=usuario, abogado_operativo=True, nombre_cargo=CARGO_DEMO, dependencia=DEPENDENCIA_DEMO,
            )
        else:
            usuario.save(update_fields=['password'])
        Auditoria.objects.create(
            usuario=usuario,
            accion='crear_abogado_demo' if es_nuevo else 'cambiar_clave_demo',
            entidad_afectada='PerfilUsuario', identificador_registro=str(usuario.pk),
        )
    permisos = Permission.objects.filter(content_type__app_label='gestion_contractual', codename__in=PERMISOS_DEMO)
    if permisos.count() != len(PERMISOS_DEMO):
        raise CommandError('Faltan permisos de la aplicación. Revise las migraciones de la base aislada.')
    if set(usuario.user_permissions.values_list('codename', flat=True)) - set(PERMISOS_DEMO):
        raise CommandError('La cuenta de demostración tiene permisos fuera del alcance previsto.')
    usuario.user_permissions.add(*permisos)
    return usuario


def aprovisionar_entidad(usuario):
    entidad = EntidadPublica.objects.filter(nit=NIT_DEMO).first()
    if entidad is None:
        if EntidadPublica.objects.filter(activo=True).exists():
            raise CommandError('La base contiene otra entidad activa. Se conserva sin cambios.')
        entidad = EntidadPublica.objects.create(nombre=ENTIDAD_DEMO, nit=NIT_DEMO)
        Auditoria.objects.create(
            usuario=usuario, accion='crear_entidad_demo', entidad_afectada='EntidadPublica',
            identificador_registro=str(entidad.pk),
        )
    elif not entidad.activo or entidad.nombre != ENTIDAD_DEMO:
        raise CommandError('El identificador de entidad está en uso y no corresponde a la demostración.')
    if Contrato.objects.exclude(entidad=entidad, abogado_responsable=usuario).exists():
        raise CommandError('Existen expedientes fuera de la demostración. La carga no modificará su entorno.')
    return entidad


def verificar_catalogo(flujo):
    estados = EstadoContrato.objects.filter(activo=True, codigo__in=flujo).in_bulk(field_name='codigo')
    if set(estados) != set(flujo):
        raise CommandError('Faltan estados activos para los ejemplos ficticios.')
    for origen, destino in zip(flujo, flujo[1:]):
        if not TransicionEstado.objects.filter(origen=estados[origen], destino=estados[destino], activa=True).exists():
            raise CommandError(f'Falta la transición aprobada de {origen} a {destino}. Se conserva el catálogo.')
    return estados


def _leer_plantilla_institucional(tipo):
    raiz = settings.BASE_DIR.parent / 'plantillas_institucionales'
    if tipo == TipoRequerimiento.OBSERVACIONES:
        carpeta = raiz / 'observaciones/v1'
        ficha = json.loads((carpeta / 'inventario.json').read_text(encoding='utf-8'))
        archivo_preparado = carpeta / ficha['plantilla']
        hash_aprobado = ficha['sha256_plantilla']
    else:
        carpeta = raiz / PLANTILLAS[tipo]
        manifiesto = json.loads((carpeta / 'inventario.json').read_text(encoding='utf-8'))
        ficha = next(f for f in manifiesto if (f.get('id') == 1 if tipo == PRIMERO else f.get('tipo') == tipo))
        archivo_preparado = carpeta / ficha['preparada'].replace('\\', '/')
        hash_aprobado = ficha['sha256_preparada']
    nombre_archivo = archivo_preparado.name
    contenido = archivo_preparado.read_bytes()
    if sha256(contenido).hexdigest() != hash_aprobado:
        raise CommandError(f'La copia aprobada de la plantilla {tipo} no coincide con el inventario.')
    return nombre_archivo, contenido


def incorporar_plantilla(tipo, usuario, *, ficticia=False):
    if ficticia:
        nombre_archivo, contenido = plantilla_ficticia(tipo)
    else:
        nombre_archivo, contenido = _leer_plantilla_institucional(tipo)
    procedencia = 'ficticia' if ficticia else 'institucional'
    activa = PlantillaDocumento.objects.filter(tipo=tipo, activa=True).first()
    if activa:
        if activa.incorporada_por_id != usuario.pk or activa.nombre_original != nombre_archivo:
            raise CommandError(f'Existe otra plantilla activa de {tipo}. Se conserva sin cambios.')
        with activa.archivo.open('rb') as archivo:
            if sha256(archivo.read()).digest() != sha256(contenido).digest():
                raise CommandError(f'La copia privada activa de {tipo} no coincide con la plantilla {procedencia}.')
        return activa
    return registrar_plantilla(
        usuario=usuario, tipo=tipo, nombre=f'SIGELC demo · {tipo} · plantilla {procedencia}',
        archivo=SimpleUploadedFile(nombre_archivo, contenido), activar=True,
    )


def calendario_para_simulacion(anio, usuario):
    if vigente(anio):
        return
    propuesta = proponer(
        anio=anio, usuario=usuario, fuente='https://jurinfo.jep.gov.co/Normograma/compilacion/docs/ley_0051_1983.htm',
        motivo='Simulación técnica de calendario en base de demostración ficticia.',
        operacion=clave_operacion(f'calendario-propuesta:{anio}'),
    )
    aprobar(
        propuesta_id=propuesta.pk, usuario=usuario, confirmado=True,
        fundamento='Simulación técnica en la base aislada de demostración para plazos ficticios. Sin validación institucional.',
        operacion=clave_operacion(f'calendario-simulacion:{anio}'),
    )


def plan_competencia_demo(hoy, *, calendario=calendario_disponible):
    """Escoge una terminación ficticia con un límite nominal en la próxima ventana de avisos."""
    calendario_anual = cache(calendario)
    for dias in range(1, 31):
        terminacion = sumar_meses(hoy + timedelta(days=dias), -30)
        estado, propuesta = evaluar({
            'fecha_terminacion': terminacion.isoformat(), 'origen': 'ejecucion', 'sujeto': 'si',
            'regimen': 'art11_cpaca', 'pacto': 'no', 'liquidacion': 'ninguna', 'incidencias': 'no',
        }, calendario=calendario_anual)
        limite = propuesta['limite_competencia']
        if estado == Estado.PENDIENTE and limite and 1 <= (date.fromisoformat(limite) - hoy).days <= 30:
            anios = sorted({d['anio'] for d in propuesta['dependencias_calendario']['fechas']})
            return terminacion, anios
    raise CommandError('No se encontró un caso ficticio verificable con el calendario de simulación disponible.')


def soporte_docx(contrato):
    """Soporte pequeño de ejemplo, sin datos de terceros ni dependencias externas."""
    archivo = BytesIO()
    tipo_documento = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
    tipos = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             f'<Override PartName="/word/document.xml" ContentType="{tipo_documento}"/></Types>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            '</Relationships>')
    lineas = (
        'SIGELC — Soporte de ejemplo ficticio',
        f'Expediente {contrato.numero_contrato} · Vigencia {contrato.anio_contractual}',
        f'Objeto de la simulación: {contrato.objeto_contractual}',
        'Archivo de demostración para la consulta y descarga del expediente. Sin efectos contractuales.',
    )
    parrafos = ''.join(f'<w:p><w:r><w:t>{escape(linea)}</w:t></w:r></w:p>' for linea in lineas)
    cuerpo = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              f'<w:body>{parrafos}</w:body></w:document>')
    with ZipFile(archivo, 'w', compression=ZIP_DEFLATED) as paquete:
        paquete.writestr('[Content_Types].xml', tipos)
        paquete.writestr('_rels/.rels', rels)
        paquete.writestr('word/document.xml', cuerpo)
    return archivo.getvalue()


def crear_soporte(contrato, usuario, guardar):
    nombre = f'soporte-ficticio-{contrato.numero_contrato.lower()}.docx'
    documento = DocumentoContrato(
        contrato=contrato, nombre_original=nombre, tipo_documento='Soporte ficticio',
        descripcion='Ejemplo de soporte disponible y pendiente de revisión documental.',
        cargado_por=usuario, fecha_recepcion=contrato.fecha_asignacion,
        operacion_recepcion=clave_operacion(f'soporte:{contrato.numero_contrato}'),
    )
    guardar(documento.archivo, nombre, soporte_docx(contrato))
    documento.save(force_insert=True)
    Auditoria.objects.create(
        usuario=usuario, accion='recibir_documento_demo', entidad_afectada='DocumentoContrato',
        identificador_registro=str(documento.pk), datos_nuevos={'contrato_id': contrato.pk},
    )
    return documento


def crear_expediente(*, sufijo, objeto, contratista, entidad, usuario, estados, asignacion, avance,
                     hoy, soporte, anterior, guardar, plantillas, terminacion=None):
    numero = f'SIGELC-DEMO-{sufijo}'
    existentes = list(Contrato.objects.filter(numero_contrato=numero))
    if existentes:
        contrato = existentes[0]
        es_propio = Auditoria.objects.filter(
            usuario=usuario, accion='registrar_expediente_demo', entidad_afectada='Contrato',
            identificador_registro=str(contrato.pk),
        ).exists()
        if (len(existentes) != 1 or not es_propio or contrato.entidad_id != entidad.pk
                or contrato.abogado_responsable_id != usuario.pk):
            raise CommandError(f'El identificador {numero} ya está reservado por otro expediente.')
        return contrato, False

    if terminacion is None:
        terminacion = asignacion - timedelta(days=7)
        if anterior:
            terminacion = min(terminacion, date(hoy.year - 1, 12, 15))
    suscripcion = terminacion - timedelta(days=58)
    contrato = guardar_contrato(
        usuario=usuario,
        contrato=Contrato(
            entidad=entidad, numero_contrato=numero, anio_contractual=suscripcion.year,
            objeto_contractual=objeto, tipo_contrato='Prestación de servicios (ficticio)',
            modalidad_contratacion='Contratación directa (simulación)', contratista_nombre=contratista,
            contratista_identificacion=f'FICTICIO-{sufijo}', valor_inicial=Decimal('12500000.00'),
            valor_actual=Decimal('12500000.00'), fecha_suscripcion=suscripcion,
            fecha_inicio=suscripcion + timedelta(days=2), fecha_terminacion=terminacion,
            fecha_asignacion=asignacion, estado_actual=estados[PRIMERO],
            observaciones='Expediente ficticio para demostración SIGELC.',
            supervisor_nombre='Equipo de supervisión ficticio' if soporte else '',
        ),
    )
    Auditoria.objects.create(
        usuario=usuario, accion='registrar_expediente_demo', entidad_afectada='Contrato',
        identificador_registro=str(contrato.pk), datos_nuevos={'identificador_demo': numero},
    )
    instancia = iniciar_seguimiento(contrato_id=contrato.pk, usuario=usuario, sin_actuaciones_previas=True)
    documento = crear_soporte(contrato, usuario, guardar) if soporte else None
    for posicion, codigo in enumerate(FLUJO_DEMO[:avance + 1]):
        if posicion:
            cambiar_estado(
                contrato_id=contrato.pk, estado_nuevo_id=estados[codigo].pk, usuario=usuario,
                motivo='Simulación de seguimiento mediante una transición habilitada.', confirmado=True,
                instancia_esperada=instancia.pk, fecha_efectiva=min(asignacion + timedelta(days=posicion * 3), hoy),
                documentos_ids=[documento.pk] if documento and posicion == 1 else (),
            )
            instancia = contrato.instancias.get(fecha_finalizacion__isnull=True)
        if codigo in plantillas and (posicion < avance or soporte):
            generar_requerimiento(
                usuario=usuario, contrato_id=contrato.pk, tipo=codigo,
                operacion=clave_operacion(f'requerimiento:{numero}:{codigo}'), exigir_etapa_vigente=True,
            )
    contrato.refresh_from_db()
    return contrato, True


class Command(BaseCommand):
    help = 'Aprovisiona expedientes ficticios, documentos y un abogado para la demo aislada de SIGELC.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--usuario', default='abogado-demo')
        parser.add_argument('--password-env', metavar='SIGELC_DEMO_VARIABLE')
        parser.add_argument('--cambiar-clave', action='store_true')
        parser.add_argument('--requerimientos-adicionales', action='store_true')
        parser.add_argument('--caso-competencia', action='store_true',
                            help='Prepara un expediente y calendarios ficticios para verificar competencia manualmente.')
        parser.add_argument(
            '--plantillas', choices=('auto', 'institucionales', 'ficticias'), default='auto',
            help='Use plantillas ficticias portables o las copias institucionales verificadas cuando existan.',
        )

    def handle(self, *args, **options):
        modo_plantillas = options['plantillas']
        caso_competencia = options['caso_competencia']
        if caso_competencia and modo_plantillas != 'ficticias':
            raise CommandError('El caso de competencia requiere --plantillas ficticias.')
        verificar_aislamiento(modo_plantillas)
        hoy = timezone.localdate()
        if hoy.year < (1987 if caso_competencia else 1985):
            raise CommandError('La fecha local está fuera del intervalo del calendario de la demostración.')
        usuario_nombre = options['usuario'].strip()
        if not usuario_nombre:
            raise CommandError('Indique un nombre de usuario de demostración.')
        adicionales = options['requerimientos_adicionales']
        carpeta = settings.BASE_DIR.parent / 'plantillas_institucionales'
        ficticias = modo_plantillas == 'ficticias' or (modo_plantillas == 'auto' and not carpeta.exists())
        flujo = FLUJO_DEMO if adicionales else FLUJO_DEMO[:4]
        ejemplos = [
            ('001', 'Inventario técnico de espacios comunitarios', 'Consorcio ficticio Horizonte', 15, 0, True, False),
            ('002', 'Digitalización de un archivo administrativo ficticio', 'Archivo Claro ficticio', 70, 0, False, True),
            ('003', 'Diagnóstico de mantenimiento de sedes simuladas', 'Servicios Atlas ficticio', 5, 1, True, False),
            ('004', 'Apoyo a la programación cultural de la entidad ficticia', 'Colectivo ficticio Nube', 12, 3, True, True),
        ]
        if adicionales:
            ejemplos.append(
                ('005', 'Organización de una biblioteca de demostración', 'Taller Ícaro ficticio', 20, 6, True, True),
            )
        try:
            with escritura_con_archivo() as guardar:
                # Serializa dos intentos de carga y mantiene los archivos bajo compensación hasta el commit.
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(%s)', [91360216829])
                estados = verificar_catalogo(flujo)
                usuario = aprovisionar_abogado(usuario_nombre, options['password_env'], options['cambiar_clave'])
                entidad = aprovisionar_entidad(usuario)
                tipos = (PRIMERO, TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO) if adicionales else (PRIMERO,)
                plantillas = {tipo: incorporar_plantilla(tipo, usuario, ficticia=ficticias) for tipo in tipos}
                incorporar_plantilla(TipoRequerimiento.OBSERVACIONES, usuario, ficticia=ficticias)
                for anio in sorted({(hoy - timedelta(days=70)).year, hoy.year, (hoy + timedelta(days=40)).year}):
                    calendario_para_simulacion(anio, usuario)
                terminacion_competencia = None
                if caso_competencia and not Contrato.objects.filter(numero_contrato=f'SIGELC-DEMO-{SUFIJO_COMPETENCIA_DEMO}').exists():
                    terminacion_competencia, anios = plan_competencia_demo(hoy)
                    for anio in anios:
                        calendario_para_simulacion(anio, usuario)
                resultados = [crear_expediente(
                    sufijo=sufijo, objeto=objeto, contratista=contratista, entidad=entidad, usuario=usuario,
                    estados=estados, asignacion=hoy - timedelta(days=dias), avance=avance, hoy=hoy,
                    soporte=soporte, anterior=anterior, guardar=guardar, plantillas=plantillas,
                ) for sufijo, objeto, contratista, dias, avance, soporte, anterior in ejemplos]
                if caso_competencia:
                    resultados.append(crear_expediente(
                        sufijo=SUFIJO_COMPETENCIA_DEMO, objeto='Ejercicio ficticio de seguimiento de fechas de liquidación',
                        contratista='Servicios Simulados Competencia', entidad=entidad, usuario=usuario,
                        estados=estados, asignacion=hoy - timedelta(days=2), avance=0, hoy=hoy,
                        soporte=False, anterior=False, guardar=guardar, plantillas=plantillas,
                        terminacion=terminacion_competencia,
                    ))
        except ValidationError as error:
            raise CommandError(' '.join(error.messages)) from error
        except (FileNotFoundError, KeyError, StopIteration) as error:
            raise CommandError('No se encontró una copia institucional aprobada o su ficha en el inventario.') from error

        self.stdout.write(self.style.SUCCESS(f'Demostración aislada lista. Usuario: {usuario_nombre}.'))
        self.stdout.write('Plantillas ficticias portables.' if ficticias else 'Plantillas institucionales verificadas.')
        for contrato, nuevo in resultados:
            accion = 'creado' if nuevo else 'conservado'
            self.stdout.write(f'{contrato.numero_contrato} [{contrato.pk}] · {contrato.estado_actual.nombre} · {accion}')
