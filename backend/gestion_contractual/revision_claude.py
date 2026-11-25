"""Return Claude proposals to a bounded, lawyer-initiated demo review."""
from datetime import timedelta
from uuid import uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .mcp_demo import (
    NIT_DEMO, NUMERO, OPERACIONES_HOSPEDADAS, PERFIL_HOSPEDADO,
    expediente_ficticio, validar_propuestas, verificar_base, verificar_entregas_hospedadas,
)
from .revision_flujo import _contrato, _evento, _preexistente, huella

SOLICITUD = 'mcp_solicitud'
RESPUESTA = 'mcp_propuestas'
VIGENCIA = timedelta(minutes=30)
PERMISO = 'change_documentocontrato'
MAX_DOCUMENTOS = 12


def _habilitada(revision):
    if settings.SETTINGS_MODULE == PERFIL_HOSPEDADO:
        return (
            getattr(settings, 'SIGELC_DEMO', False)
            and getattr(settings, 'SIGELC_MCP_PROPUESTAS', False)
            and _es_expediente_ficticio(revision.contrato)
            and (_entrega_hospedada(revision) or revision.version > 2)
        )
    return (getattr(settings, 'SIGELC_DEMO', False)
            and revision.pk in getattr(settings, 'SIGELC_CLAUDE_ENTREGAS', ())
            and revision.contrato.numero_contrato == NUMERO)


def _operacion_respuesta(solicitud_id):
    return uuid5(solicitud_id, 'sigelc-claude-propuestas-remotas-v1')


def _coincide_contrato(solicitud, contrato):
    return (solicitud.datos.get('version_contrato'), solicitud.datos.get('estado_contrato')) == (
        contrato.version_registro, contrato.estado_actual_id,
    )


def _estado(revision, solicitud, ahora, contrato):
    respuesta = revision.eventos_revision.filter(tipo=RESPUESTA,
        operacion=_operacion_respuesta(solicitud.operacion)).first()
    vence = solicitud.fecha + VIGENCIA
    estado = 'pendiente'
    if respuesta:
        estado = 'recibida'
    elif vence <= ahora or not _coincide_contrato(solicitud, contrato):
        estado = 'vencida'
    datos = {
        'id': str(solicitud.operacion), 'vence_en': vence.isoformat(), 'estado': estado,
        'propuestas': len(respuesta.datos['hallazgos']) if respuesta else None,
    }
    if 'documentos' in solicitud.datos:
        datos['documentos'] = solicitud.datos['documentos']
    return datos


def _solicitudes(revision, usuario):
    return revision.eventos_revision.filter(tipo=SOLICITUD, usuario=usuario)


def _entrega_hospedada(revision):
    return (
        revision.pk in getattr(settings, 'SIGELC_MCP_ENTREGAS', ())
        and (revision.version, revision.operacion) in OPERACIONES_HOSPEDADAS
    )


def _es_expediente_ficticio(contrato):
    return (contrato.numero_contrato, contrato.contratista_identificacion, contrato.entidad.nit) == (
        NUMERO, 'FICTICIO-001', NIT_DEMO,
    )


def _documentos_disponibles(revision):
    return list(revision.fuentes_revision.exclude(tipo='zip').exclude(fragmentos=[]).order_by('pk')
                .values_list('pk', flat=True))


def estado_claude(revision, usuario, *, analizada, es_vigente, puede_decidir):
    entorno_hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
    es_demo = getattr(settings, 'SIGELC_DEMO', False) and _es_expediente_ficticio(revision.contrato)
    puede_consultar = (
        es_demo
        and (entorno_hospedado or getattr(settings, 'SIGELC_CLAUDE_CONSULTA_LOCAL', False))
        and revision.version in (1, 2)
    )
    if entorno_hospedado:
        puede_consultar = puede_consultar and _entrega_hospedada(revision)
        propias = (revision.version <= 2 or (usuario is not None and revision.usuario_id == usuario.pk))
        admite_solicitud = es_demo and propias and _habilitada(revision)
        if not puede_consultar and not admite_solicitud:
            return None
        datos = {
            'puede_consultar': puede_consultar, 'puede_solicitar': False, 'solicitud': None,
            'conector_url': f'https://{settings.ALLOWED_HOSTS[0]}/mcp',
        }
        if admite_solicitud:
            solicitud = _solicitudes(revision, usuario).last()
            estado = _estado(revision, solicitud, timezone.now(), revision.contrato) if solicitud else None
            if estado and estado['estado'] == 'pendiente' and not es_vigente:
                estado['estado'] = 'vencida'
            documentos = _documentos_disponibles(revision) if analizada and es_vigente and puede_decidir else []
            datos.update(
                requiere_seleccion=True, documentos_disponibles=documentos, solicitud=estado,
                puede_solicitar=bool(documentos and (estado is None or estado['estado'] != 'pendiente')),
            )
        return datos
    if not _habilitada(revision):
        if not puede_consultar:
            return None
        return {'puede_consultar': True, 'puede_solicitar': False, 'solicitud': None}
    solicitud = _solicitudes(revision, usuario).last()
    estado = _estado(revision, solicitud, timezone.now(), revision.contrato) if solicitud else None
    return {
        'puede_consultar': puede_consultar,
        'puede_solicitar': bool(puede_decidir and analizada and es_vigente
                               and (estado is None or estado['estado'] == 'vencida')),
        'solicitud': estado,
    }


def _validar_acceso(revision, usuario):
    verificar_base()
    if not _habilitada(revision):
        raise ValidationError('La entrega no está habilitada para el piloto Claude.')
    contrato = _contrato(usuario, revision.contrato_id, PERMISO)
    if expediente_ficticio(usuario).pk != contrato.pk:
        raise ValidationError('El expediente no pertenece al piloto ficticio.')
    if settings.SETTINGS_MODULE == PERFIL_HOSPEDADO:
        if revision.version <= 2:
            verificar_entregas_hospedadas(contrato, (revision.pk,))
        elif revision.usuario_id != usuario.pk:
            raise ValidationError('Solo puede compartir entregas que registró con su propia cuenta.')
    return contrato


def _validar_vigente(revision, contrato):
    if (revision.pk != contrato.revisiones_inteligentes.first().pk
            or not revision.eventos_revision.filter(tipo='analisis').exists()):
        raise ValidationError('Use la última entrega del expediente y ejecute antes las verificaciones locales.')


def _solicitud_autorizada(usuario, revision, solicitud_id):
    solicitud = _solicitudes(revision, usuario).filter(operacion=solicitud_id).first()
    if solicitud is None:
        raise ValidationError('La solicitud no pertenece a esta entrega y al abogado autenticado.')
    return solicitud


def _validar_solicitud_vigente(solicitud, revision, contrato):
    _validar_vigente(revision, contrato)
    if solicitud.fecha + VIGENCIA <= timezone.now():
        raise ValidationError('La solicitud de Claude venció. Genere otra en SIGELC.')
    if not _coincide_contrato(solicitud, contrato):
        raise ValidationError('El expediente cambió. Inicie una nueva solicitud en SIGELC.')


@transaction.atomic
def documentos_autorizados(*, usuario, revision, solicitud_id):
    contrato = _validar_acceso(revision, usuario)
    solicitud = _solicitud_autorizada(usuario, revision, solicitud_id)
    _validar_solicitud_vigente(solicitud, revision, contrato)
    if (not solicitud.datos.get('ficticios') or not solicitud.datos.get('documentos') or revision.eventos_revision.filter(
            tipo=RESPUESTA, operacion=_operacion_respuesta(solicitud.operacion),
    ).exists()):
        raise ValidationError('La solicitud no contiene una selección de documentos pendiente.')
    return tuple(solicitud.datos['documentos'])


@transaction.atomic
def solicitar(*, usuario, revision, operacion, documentos=None, confirmado=False, ficticios=False):
    contrato = _validar_acceso(revision, usuario)
    _validar_vigente(revision, contrato)
    hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
    payload = {}
    if hospedado:
        if not confirmado or not ficticios or not documentos or not 1 <= len(documentos) <= MAX_DOCUMENTOS:
            raise ValidationError('Seleccione y confirme entre 1 y 12 documentos ficticios.')
        if len(set(documentos)) != len(documentos) or not set(documentos).issubset(_documentos_disponibles(revision)):
            raise ValidationError('Seleccione documentos distintos y con texto en esta entrega ficticia.')
        payload.update(documentos=sorted(documentos), ficticios=True)
    elif documentos is not None or confirmado or ficticios:
        raise ValidationError('La selección explícita solo está disponible en la demo hospedada.')
    firma = huella(['solicitar-claude', revision.pk, payload] if hospedado else ['solicitar-claude', revision.pk])
    previo = _preexistente(revision, usuario, SOLICITUD, operacion, firma)
    if previo:
        return previo
    actual = _solicitudes(revision, usuario).last()
    if actual:
        estado = _estado(revision, actual, timezone.now(), contrato)['estado']
        if (not hospedado and estado != 'vencida') or (hospedado and estado == 'pendiente'):
            if hospedado and actual.datos.get('documentos') != payload['documentos']:
                raise ValidationError('Ya existe una solicitud pendiente con otra selección de documentos.')
            return actual
    return _evento(revision, usuario, SOLICITUD, operacion, firma, {
        'version_contrato': contrato.version_registro, 'estado_contrato': contrato.estado_actual_id,
        **payload,
    })


@transaction.atomic
def recibir_propuestas(*, usuario, revision, solicitud_id, propuestas):
    contrato = _validar_acceso(revision, usuario)
    solicitud = _solicitud_autorizada(usuario, revision, solicitud_id)
    operacion = _operacion_respuesta(solicitud_id)
    firma = huella([str(solicitud_id), [p.model_dump(mode='json') for p in propuestas]])
    previo = _preexistente(revision, usuario, RESPUESTA, operacion, firma)
    if previo:
        return {'solicitud_id': str(solicitud_id), 'estado': 'recibida',
                'propuestas': len(previo.datos['hallazgos'])}
    _validar_solicitud_vigente(solicitud, revision, contrato)
    documentos = solicitud.datos.get('documentos')
    if settings.SETTINGS_MODULE == PERFIL_HOSPEDADO and (not documentos or not solicitud.datos.get('ficticios')):
        raise ValidationError('La solicitud no contiene una selección de documentos.')
    hallazgos = validar_propuestas(revision, operacion, propuestas, documentos=documentos)
    _evento(revision, usuario, RESPUESTA, operacion, firma, {
        'hallazgos': hallazgos, 'solicitud_id': str(solicitud_id),
        'origen': 'claude_desktop', 'modelo': 'no_informado',
    })
    return {'solicitud_id': str(solicitud_id), 'estado': 'recibida', 'propuestas': len(hallazgos)}
