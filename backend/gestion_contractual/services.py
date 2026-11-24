from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from .access import es_abogado_operativo
from .calendario import calcular_vencimiento, calendario_disponible
from .models import Auditoria, Contrato, DocumentoContrato, EstadoContrato, HistorialEstadoContrato, InstanciaSeguimiento, SoporteCambioEstado, TransicionEstado


@transaction.atomic
def guardar_contrato(*, contrato, usuario, campos_editados=()):
    """Persiste un contrato que ya pasó los controles de acceso de su interfaz."""
    from .calendarios_servicios import bloquear_calendarios
    from .competencia import actualizar_por_edicion, datos_contrato
    from .integridad import valores

    nuevo = contrato._state.adding
    if nuevo:
        contrato.abogado_responsable = usuario
    bloquear_calendarios()
    contrato.save()
    if not nuevo and set(campos_editados) & datos_contrato(contrato).keys():
        actualizar_por_edicion(contrato, usuario)
    Auditoria.objects.create(
        usuario=usuario,
        accion='crear_contrato' if nuevo else 'editar_contrato',
        entidad_afectada='Contrato',
        identificador_registro=str(contrato.pk),
        datos_anteriores=contrato._valores_anteriores,
        datos_nuevos=valores(contrato),
    )
    return contrato


def validar_inicio_seguimiento(contrato):
    if not contrato.fecha_asignacion or contrato.fecha_asignacion > timezone.localdate():
        raise ValidationError('Indique una fecha real de asignación que no sea futura.')
    if contrato.estado_actual.codigo != 'primer-requerimiento' or not contrato.estado_actual.activo:
        raise ValidationError('El inicio directo requiere Primer requerimiento activo.')
    if not contrato.entidad.activo:
        raise ValidationError('Se requiere una entidad activa.')
    if contrato.pk and (contrato.historial_estados.exists() or contrato.instancias.exists()):
        raise ValidationError('El expediente tiene seguimiento o actuaciones previas y requiere revisión cronológica.')


def vencimiento_etapa(estado, inicio):
    try:
        return calcular_vencimiento(inicio, estado.plazo_dias_habiles, proveedor=calendario_disponible)
    except (ValueError, OverflowError) as error:
        raise ValidationError('No hay un calendario válido para calcular el plazo de esta etapa.') from error


@transaction.atomic
def iniciar_seguimiento(*, contrato_id, usuario, sin_actuaciones_previas):
    from .calendarios_servicios import bloquear_calendarios
    bloquear_calendarios()
    if not es_abogado_operativo(usuario) or not usuario.has_perm('gestion_contractual.change_contrato'):
        raise PermissionDenied('Se requiere el abogado operativo autorizado.')
    if sin_actuaciones_previas is not True:
        raise ValidationError('Confirme el inicio efectivo de la etapa y la ausencia de actuaciones previas.')
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    validar_inicio_seguimiento(contrato)
    vence, procedencia = vencimiento_etapa(contrato.estado_actual, contrato.fecha_asignacion)
    instancia = InstanciaSeguimiento(
        contrato=contrato, estado=contrato.estado_actual, orden=1, usuario=usuario,
        fecha_inicio=contrato.fecha_asignacion, fecha_vencimiento=vence,
        plazo_dias_habiles=contrato.estado_actual.plazo_dias_habiles, procedencia_calendario=procedencia,
    )
    instancia._por_servicio = True
    instancia.save(force_insert=True)
    Auditoria.objects.create(usuario=usuario, accion='iniciar_seguimiento', entidad_afectada='Contrato',
                             identificador_registro=str(contrato.pk), datos_nuevos={'instancia_id': instancia.pk})
    return instancia


@transaction.atomic
def cambiar_estado(*, contrato_id, estado_nuevo_id, usuario, motivo, confirmado, instancia_esperada=None,
                   fecha_efectiva=None, observaciones='', documentos_ids=()):
    from .calendarios_servicios import bloquear_calendarios
    bloquear_calendarios()
    if not es_abogado_operativo(usuario) or not usuario.has_perm('gestion_contractual.change_contrato'):
        raise PermissionDenied('Se requiere el abogado operativo autorizado.')
    if confirmado is not True:
        raise ValidationError('Debe confirmar expresamente el cambio de estado.')
    if not motivo or not motivo.strip():
        raise ValidationError('El motivo es obligatorio.')
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    destino = EstadoContrato.objects.get(pk=estado_nuevo_id, activo=True)
    if destino.codigo == 'liquidado' or destino.nombre.strip().casefold() == 'liquidado':
        raise ValidationError('La liquidación está bloqueada hasta definir y validar los controles jurídicos, documentales y financieros.')
    if destino.codigo == 'cierre' or destino.nombre.strip().casefold() == 'cierre':
        raise ValidationError('El cierre está bloqueado hasta definir sus reglas específicas.')
    if not contrato.estado_actual.activo or not TransicionEstado.objects.filter(origen=contrato.estado_actual, destino=destino, activa=True).exists():
        raise ValidationError('La transición no está habilitada.')
    if HistorialEstadoContrato.objects.filter(contrato=contrato, resultado=HistorialEstadoContrato.Resultado.PENDIENTE).exists():
        raise ValidationError('Existe una solicitud anterior pendiente; debe resolverse antes de continuar.')
    actual = contrato.instancias.filter(fecha_finalizacion__isnull=True).first()
    if actual is None:
        raise ValidationError('El expediente está en borrador. Inicie o reconstruya el seguimiento antes de avanzar.')
    if actual.pk != instancia_esperada or actual.estado_id != contrato.estado_actual_id:
        raise ValidationError('La etapa vigente cambió. Recargue el contrato antes de confirmar.')
    inicio = fecha_efectiva if fecha_efectiva is not None else timezone.localdate()
    if not actual.fecha_inicio <= inicio <= timezone.localdate():
        raise ValidationError('La fecha efectiva debe estar entre el inicio de la etapa vigente y hoy.')
    vence, procedencia = vencimiento_etapa(destino, inicio)
    ids = set(documentos_ids or ())
    if ids and not usuario.has_perm('gestion_contractual.view_documentocontrato'):
        raise PermissionDenied('No tiene permiso para vincular documentos.')
    documentos = list(DocumentoContrato.objects.select_for_update().filter(pk__in=ids, contrato=contrato, activo=True))
    if len(documentos) != len(ids):
        raise ValidationError('Los soportes deben estar activos y pertenecer a este contrato.')
    anterior = contrato.estado_actual_id
    ahora = timezone.now()
    registro = HistorialEstadoContrato(contrato=contrato, estado_anterior_id=anterior, estado_nuevo=destino, solicitado_por=usuario, aprobado_por=usuario, fecha_aprobacion=ahora, fecha_inicio_plazo=inicio, fecha_vencimiento_plazo=vence, motivo=motivo.strip(), observaciones=observaciones, resultado=HistorialEstadoContrato.Resultado.APROBADO)
    registro._por_servicio = True
    registro.save(force_insert=True)
    models.QuerySet.update(InstanciaSeguimiento.objects.filter(pk=actual.pk), fecha_finalizacion=inicio)
    nueva = InstanciaSeguimiento(
        contrato=contrato, estado=destino, orden=actual.orden + 1, usuario=usuario,
        fecha_inicio=inicio, fecha_vencimiento=vence, plazo_dias_habiles=destino.plazo_dias_habiles,
        evento_origen=registro, procedencia_calendario=procedencia,
    )
    nueva._por_servicio = True
    nueva.save(force_insert=True)
    for documento in documentos:
        soporte = SoporteCambioEstado(historial=registro, documento=documento)
        soporte._por_servicio = True
        soporte.save(force_insert=True)
    models.QuerySet.update(Contrato.objects.filter(pk=contrato.pk), estado_actual=destino, fecha_actualizacion=ahora, version_registro=models.F('version_registro') + 1)
    Auditoria.objects.create(usuario=usuario, accion='cambio_estado', entidad_afectada='Contrato', identificador_registro=str(contrato.pk), datos_anteriores={'estado_id': anterior, 'instancia_id': actual.pk}, datos_nuevos={'estado_id': destino.pk, 'historial_id': registro.pk, 'documentos_ids': sorted(ids), 'instancia_id': nueva.pk})
    return registro
