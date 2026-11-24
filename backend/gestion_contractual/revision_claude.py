"""Return Claude proposals to a bounded, lawyer-initiated demo review."""
from datetime import timedelta
from uuid import uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .mcp_demo import (
    NIT_DEMO, NUMERO, OPERACIONES_HOSPEDADAS, PERFIL_HOSPEDADO,
    expediente_ficticio, validar_propuestas, verificar_base,
)
from .revision_flujo import _contrato, _evento, _preexistente, huella

SOLICITUD = 'mcp_solicitud'
RESPUESTA = 'mcp_propuestas'
VIGENCIA = timedelta(minutes=30)
PERMISO = 'change_documentocontrato'


def _habilitada(revision):
    return (getattr(settings, 'SIGELC_DEMO', False)
            and revision.pk in getattr(settings, 'SIGELC_CLAUDE_ENTREGAS', ())
            and revision.contrato.numero_contrato == NUMERO)


def _operacion_respuesta(solicitud_id):
    return uuid5(solicitud_id, 'sigelc-claude-propuestas-remotas-v1')


def _coincide_contrato(solicitud, contrato):
    return solicitud.datos == {
        'version_contrato': contrato.version_registro, 'estado_contrato': contrato.estado_actual_id,
    }


def _estado(revision, solicitud, ahora, contrato):
    respuesta = revision.eventos_revision.filter(tipo=RESPUESTA,
        operacion=_operacion_respuesta(solicitud.operacion)).first()
    vence = solicitud.fecha + VIGENCIA
    estado = 'pendiente'
    if respuesta:
        estado = 'recibida'
    elif vence <= ahora or not _coincide_contrato(solicitud, contrato):
        estado = 'vencida'
    return {
        'id': str(solicitud.operacion), 'vence_en': vence.isoformat(), 'estado': estado,
        'propuestas': len(respuesta.datos['hallazgos']) if respuesta else None,
    }


def _solicitudes(revision, usuario):
    return revision.eventos_revision.filter(tipo=SOLICITUD, usuario=usuario)


def estado_claude(revision, usuario, *, analizada, es_vigente, puede_decidir):
    entorno_hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
    puede_consultar = (
        getattr(settings, 'SIGELC_DEMO', False)
        and (entorno_hospedado or getattr(settings, 'SIGELC_CLAUDE_CONSULTA_LOCAL', False))
        and revision.contrato.numero_contrato == NUMERO
        and revision.contrato.contratista_identificacion == 'FICTICIO-001'
        and revision.contrato.entidad.nit == NIT_DEMO
        and revision.version in (1, 2)
    )
    if entorno_hospedado and puede_consultar:
        if (revision.pk not in settings.SIGELC_MCP_ENTREGAS
                or (revision.version, revision.operacion) not in OPERACIONES_HOSPEDADAS):
            return None
        return {
            'puede_consultar': True, 'puede_solicitar': False, 'solicitud': None,
            'conector_url': f'https://{settings.ALLOWED_HOSTS[0]}/mcp',
        }
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
    return contrato


def _validar_vigente(revision, contrato):
    if (revision.pk != contrato.revisiones_inteligentes.first().pk
            or not revision.eventos_revision.filter(tipo='analisis').exists()):
        raise ValidationError('Use la última entrega del expediente y ejecute antes las verificaciones locales.')


@transaction.atomic
def solicitar(*, usuario, revision, operacion):
    contrato = _validar_acceso(revision, usuario)
    _validar_vigente(revision, contrato)
    firma = huella(['solicitar-claude', revision.pk])
    previo = _preexistente(revision, usuario, SOLICITUD, operacion, firma)
    if previo:
        return previo
    actual = _solicitudes(revision, usuario).last()
    if actual and _estado(revision, actual, timezone.now(), contrato)['estado'] != 'vencida':
        return actual
    return _evento(revision, usuario, SOLICITUD, operacion, firma, {
        'version_contrato': contrato.version_registro, 'estado_contrato': contrato.estado_actual_id,
    })


@transaction.atomic
def recibir_propuestas(*, usuario, revision, solicitud_id, propuestas):
    contrato = _validar_acceso(revision, usuario)
    solicitud = _solicitudes(revision, usuario).filter(operacion=solicitud_id).first()
    if solicitud is None:
        raise ValidationError('La solicitud no pertenece a esta entrega y al abogado autenticado.')
    operacion = _operacion_respuesta(solicitud_id)
    firma = huella([str(solicitud_id), [p.model_dump(mode='json') for p in propuestas]])
    previo = _preexistente(revision, usuario, RESPUESTA, operacion, firma)
    if previo:
        return {'solicitud_id': str(solicitud_id), 'estado': 'recibida',
                'propuestas': len(previo.datos['hallazgos'])}
    _validar_vigente(revision, contrato)
    if solicitud.fecha + VIGENCIA <= timezone.now():
        raise ValidationError('La solicitud de Claude venció. Genere otra en SIGELC.')
    if not _coincide_contrato(solicitud, contrato):
        raise ValidationError('El expediente cambió. Inicie una nueva solicitud en SIGELC.')
    hallazgos = validar_propuestas(revision, operacion, propuestas)
    _evento(revision, usuario, RESPUESTA, operacion, firma, {
        'hallazgos': hallazgos, 'solicitud_id': str(solicitud_id),
        'origen': 'claude_desktop', 'modelo': 'no_informado',
    })
    return {'solicitud_id': str(solicitud_id), 'estado': 'recibida', 'propuestas': len(hallazgos)}
