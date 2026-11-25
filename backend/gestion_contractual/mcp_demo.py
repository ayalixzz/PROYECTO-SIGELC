"""Scopes and evidence validation for the isolated Claude connector."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import NAMESPACE_URL, UUID, uuid5

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils.crypto import constant_time_compare

from .access import es_abogado_operativo
from .models import Auditoria, Contrato
from .revision_analisis import _referencias
from .revision_flujo import _contrato, _evento, _preexistente, analisis_registrado, huella

ARCHIVO_AUTORIZACION = Path(settings.BASE_DIR) / '.sigelc-mcp.dpapi'
SALT = 'sigelc.mcp.demo.v1'
VIGENCIA = 8 * 3600
NUMERO = 'SIGELC-DEMO-001'
NIT_DEMO = 'SIGELC-DEMO-000'
PERFIL_HOSPEDADO = 'config.settings_hospedado'
PERMISOS = ('gestion_contractual.view_contrato', 'gestion_contractual.view_documentocontrato')
# Identificadores de operación de las dos cargas ficticias iniciales de Render.
OPERACIONES_HOSPEDADAS = frozenset(
    (version, uuid5(NAMESPACE_URL, f'urn:sigelc:demo:v1:hospedada-paquete-{version}'))
    for version in (1, 2)
)


def verificar_base():
    if not getattr(settings, 'SIGELC_DEMO', False):
        raise ValidationError('Se requiere la configuración PostgreSQL aislada de la demo.')
    motor = connection.settings_dict['ENGINE']
    if settings.SETTINGS_MODULE == PERFIL_HOSPEDADO:
        from config.postgres_supabase import DATABASE_NAME, DATABASE_ROLE, SCHEMA

        entregas = getattr(settings, 'SIGELC_MCP_ENTREGAS', ())
        if (motor != 'config.postgres_supabase' or settings.DEBUG
                or not 1 <= len(entregas) <= 4 or len(entregas) != len(set(entregas))
                or any(type(pk) is not int or pk <= 0 for pk in entregas)):
            raise ValidationError('El conector hospedado requiere Supabase aislado y entregas autorizadas.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_user, current_schema()')
            if (cursor.fetchone() != (DATABASE_NAME, DATABASE_ROLE, SCHEMA)
                    or not connection.connection.pgconn.ssl_in_use):
                raise ValidationError('La identidad o TLS de la base hospedada no coincide.')
        return
    if motor != 'config.postgres_demo':
        raise ValidationError('Se requiere la configuración PostgreSQL aislada de la demo.')
    from config.postgres_demo import DATABASE_NAME, DATABASE_OID

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), oid FROM pg_database WHERE datname = current_database()')
        if cursor.fetchone() != (DATABASE_NAME, DATABASE_OID):
            raise ValidationError('La identidad de la base aislada no coincide.')


def leer_autorizacion():
    import win32crypt

    try:
        with ARCHIVO_AUTORIZACION.open('rb') as archivo:
            cifrada = archivo.read(8193)
    except FileNotFoundError:
        return ''
    if len(cifrada) > 8192:
        raise ValidationError('La autorización local es inválida.')
    return win32crypt.CryptUnprotectData(cifrada, None, None, None, 1)[1].decode('utf-8')


def guardar_autorizacion(permiso):
    import win32crypt

    firma = signing.dumps(permiso, salt=SALT).encode()
    cifrada = win32crypt.CryptProtectData(firma, None, None, None, None, 1)
    with NamedTemporaryFile(dir=ARCHIVO_AUTORIZACION.parent, prefix='.sigelc-mcp-', suffix='.dpapi',
                            delete=False) as archivo:
        temporal = Path(archivo.name)
        archivo.write(cifrada)
    try:
        temporal.replace(ARCHIVO_AUTORIZACION)
    finally:
        temporal.unlink(missing_ok=True)


def expediente_ficticio(usuario):
    if not es_abogado_operativo(usuario) or usuario.is_superuser or not usuario.has_perms(PERMISOS):
        raise ValidationError('El abogado operativo carece de acceso al conector.')
    candidatos = Contrato.objects.filter(
        abogado_responsable=usuario, numero_contrato=NUMERO, contratista_identificacion='FICTICIO-001',
    )
    hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
    if hospedado:
        candidatos = candidatos.filter(entidad__nit=NIT_DEMO)
    contrato = candidatos.select_related('estado_actual').first()
    if not contrato:
        raise ValidationError('No se encontró el expediente ficticio provisionado para este usuario.')
    auditoria = Auditoria.objects.filter(
        usuario=usuario, accion='registrar_expediente_demo', entidad_afectada='Contrato',
        identificador_registro=str(contrato.pk),
    )
    if hospedado:
        auditoria = auditoria.filter(datos_nuevos__identificador_demo=NUMERO)
    if not auditoria.exists():
        raise ValidationError('No se encontró el expediente ficticio provisionado para este usuario.')
    return contrato


def acceso():
    if connection.settings_dict['ENGINE'] != 'config.postgres_demo':
        raise ValidationError('La autorización DPAPI se usa exclusivamente con la demo local de Windows.')
    verificar_base()
    credencial = leer_autorizacion()
    try:
        permiso = signing.loads(credencial or '', salt=SALT, max_age=VIGENCIA)
    except signing.BadSignature as error:
        raise ValidationError('Autorice localmente el conector SIGELC en Windows.') from error
    usuario = get_user_model().objects.filter(pk=permiso['usuario']).first()
    if not usuario or not constant_time_compare(usuario.get_session_auth_hash(), permiso['autenticacion']):
        raise ValidationError('La cuenta cambió. Autorice nuevamente el conector.')
    contrato = expediente_ficticio(usuario)
    if contrato.pk != permiso['contrato'] or contrato.version_registro != permiso['version_contrato']:
        raise ValidationError('El expediente cambió. Autorice nuevamente los datos de la demo.')
    return usuario, contrato, permiso


def verificar_entregas_hospedadas(contrato, entregas):
    filas = set(contrato.revisiones_inteligentes.filter(pk__in=entregas).values_list('version', 'operacion'))
    if len(filas) != len(entregas) or not filas.issubset(OPERACIONES_HOSPEDADAS):
        raise ValidationError('Solo se admiten las entregas ficticias originales de la demo hospedada.')


def entrega(contrato, permiso, entrega_id):
    if entrega_id not in permiso['entregas']:
        raise ValidationError('Entrega no autorizada.')
    revision = contrato.revisiones_inteligentes.filter(pk=entrega_id).first()
    if not revision:
        raise ValidationError('Entrega no autorizada.')
    return revision


def catalogo_juridico():
    return _referencias({'referencias_juridicas': getattr(settings, 'SIGELC_REFERENCIAS_REVISION', [])})


def validar_propuestas(revision, operacion, propuestas, *, documentos=None):
    referencias = catalogo_juridico()
    disponibles = revision.fuentes_revision.exclude(tipo='zip')
    if documentos is not None:
        disponibles = disponibles.filter(pk__in=documentos)
    fuentes = {f.pk: f for f in disponibles}
    resultado = []
    for indice, propuesta in enumerate(propuestas):
        evidencia = []
        for cita in propuesta.evidencia:
            fuente = fuentes.get(cita.documento_id)
            if not fuente or not cita.fragmento.strip() or not any(
                f['ubicacion'] == cita.ubicacion and cita.fragmento in f['texto'] for f in fuente.fragmentos
            ):
                raise ValidationError('La cita no existe en el documento y la ubicación indicados.')
            evidencia.append({**cita.model_dump(), 'nombre': fuente.nombre})
        if propuesta.referencia_juridica and propuesta.referencia_juridica not in referencias:
            raise ValidationError('La referencia jurídica no está en el catálogo verificado.')
        resultado.append({
            'id': f'mcp-{operacion.hex}-{indice}', 'descripcion': propuesta.descripcion,
            'categoria': propuesta.categoria, 'evidencia': evidencia,
            'regla': referencias.get(propuesta.referencia_juridica), 'origen': 'claude_desktop',
            'estado': 'PENDIENTE_REVISION', 'incertidumbre': propuesta.incertidumbre,
            'justificacion': propuesta.fundamento, 'recomendacion': propuesta.recomendacion,
        })
    return resultado


def firma_propuesta(revision, operacion, propuestas):
    return huella([revision.contrato_id, revision.pk, str(operacion), [p.model_dump() for p in propuestas]])


@transaction.atomic
def guardar(usuario, revision, permiso, operacion: UUID, propuestas):
    contrato = _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
    if not permiso.get('escritura') or contrato.version_registro != permiso['version_contrato']:
        raise ValidationError('La autorización actual solo permite lectura o el contrato cambió.')
    firma = firma_propuesta(revision, operacion, propuestas)
    previo = _preexistente(revision, usuario, 'mcp_propuestas', operacion, firma)
    if previo:
        return previo
    if revision.pk != contrato.revisiones_inteligentes.first().pk or not analisis_registrado(revision):
        raise ValidationError('Seleccione la última entrega analizada.')
    hallazgos = validar_propuestas(revision, operacion, propuestas)
    if permiso.get('aprobacion') != [str(operacion), firma]:
        raise ValidationError(f'Autorice localmente esta operación {operacion} con la huella {firma}.')
    return _evento(revision, usuario, 'mcp_propuestas', operacion, firma, {
        'hallazgos': hallazgos, 'origen': 'claude_desktop', 'modelo': 'no_informado',
    })
