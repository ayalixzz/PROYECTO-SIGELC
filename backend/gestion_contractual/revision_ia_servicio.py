"""Consent, idempotency and audit for optional third-party model calls."""

from datetime import timedelta
from uuid import uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .revision_analisis import proponer_con_ia
from .revision_flujo import (
    _contrato, _evento, _preexistente, contexto_contractual_minimo, documentos_analizables, huella,
)
from .revision_proveedores import proveedor_desde_entorno


def proveedor_configurado():
    if not getattr(settings, 'SIGELC_DEMO', False) or not getattr(settings, 'SIGELC_IA_ENVIO_FICTICIO', False):
        return None
    nombre = getattr(settings, 'SIGELC_IA_PROVEEDOR', '')
    if nombre not in ('openai', 'anthropic'):
        return None
    try:
        proveedor = proveedor_desde_entorno(nombre)
    except ValueError:
        return None
    return (nombre, proveedor) if proveedor is not None else None


def proveedor_publico():
    configurado = proveedor_configurado()
    if configurado is None:
        return None
    nombre, proveedor = configurado
    return {'nombre': nombre, 'modelo': proveedor.version}


def _resultado(revision, solicitud):
    return revision.eventos_revision.filter(
        tipo='ia_resultado', operacion=uuid5(solicitud.operacion, 'resultado-ia'),
    ).first()


@transaction.atomic
def _reservar(*, usuario, revision, operacion, documentos, nombre, proveedor):
    contrato = _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
    firma = huella(['IA externa', sorted(documentos)])
    previo = _preexistente(revision, usuario, 'ia_solicitud', operacion, firma)
    if previo is not None:
        # A retry neither resends documents nor replaces an already recorded result.
        return None
    if not revision.eventos_revision.filter(tipo='analisis').exists():
        raise ValidationError('Ejecute primero las verificaciones locales.')
    if revision.contrato.revisiones_inteligentes.first().pk != revision.pk:
        raise ValidationError('Use la entrega vigente para un análisis externo nuevo.')
    anterior = revision.eventos_revision.filter(tipo='ia_solicitud').last()
    if anterior is not None:
        resultado = _resultado(revision, anterior)
        if resultado and resultado.datos['ia']['estado'] == 'disponible':
            raise ValidationError('La entrega ya tiene un análisis externo completado.')
        if resultado is None and timezone.now() - anterior.fecha < timedelta(minutes=5):
            raise ValidationError('Ya existe una solicitud en curso para esta entrega.')
    fuentes = [d for d in documentos_analizables(revision) if d['id'] in documentos]
    if {d['id'] for d in fuentes} != set(documentos) or any(not d['fragmentos'] for d in fuentes):
        raise ValidationError('Seleccione fuentes de esta entrega que tengan texto extraído.')
    contexto = {
        **contexto_contractual_minimo(contrato),
        'envio_autorizado': True,
        'documentos_autorizados': documentos,
        'referencias_juridicas': getattr(settings, 'SIGELC_REFERENCIAS_REVISION', []),
    }
    solicitud = _evento(revision, usuario, 'ia_solicitud', operacion, firma, {
        'proveedor': nombre, 'modelo': proveedor.version, 'consentimiento_ficticio': True,
        'fuentes': [{'id': d['id'], 'sha256': d['sha256']} for d in fuentes],
        'version_contrato': contrato.version_registro,
    })
    return solicitud, fuentes, contexto


def analizar_externamente(*, usuario, revision, operacion, documentos, confirmado):
    if confirmado is not True or not documentos or len(documentos) != len(set(documentos)):
        raise ValidationError('Confirme el envío de fuentes ficticias y seleccione sus documentos una sola vez.')
    configurado = proveedor_configurado()
    if configurado is None:
        raise ValidationError('El proveedor externo no está habilitado para la demo.')
    nombre, proveedor = configurado
    reserva = _reservar(usuario=usuario, revision=revision, operacion=operacion,
        documentos=documentos, nombre=nombre, proveedor=proveedor)
    if reserva is None:
        return
    solicitud, fuentes, contexto = reserva
    # Avoid keeping a contract lock while waiting for the provider.
    datos = proponer_con_ia(fuentes, contexto, proveedor)
    with transaction.atomic():
        _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
        if revision.eventos_revision.filter(tipo='ia_solicitud').last().pk != solicitud.pk:
            return
        _evento(revision, usuario, 'ia_resultado', uuid5(operacion, 'resultado-ia'), solicitud.huella, datos)
