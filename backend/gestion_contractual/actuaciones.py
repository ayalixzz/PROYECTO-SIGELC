"""Registrar trabajo del expediente sin cambiar estados ni enviar comunicaciones."""
from hashlib import sha256
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.http import Http404
from django.utils import timezone

from .actuaciones_models import ActuacionContractual, EventoActuacionContractual
from .integridad import ConflictoEdicion, valores
from .models import Auditoria, Contrato, DocumentoContrato
from .requerimientos import exigir


def _firma(datos):
    serializado = json.dumps(datos, ensure_ascii=False, sort_keys=True, default=str).encode()
    return sha256(serializado).hexdigest()


def _soporte(contrato, usuario, documento_id):
    if documento_id is None:
        return None
    if not usuario.has_perm('gestion_contractual.view_documentocontrato'):
        raise PermissionDenied('Su cuenta no tiene acceso a los soportes del expediente.')
    soporte = DocumentoContrato.objects.select_for_update().filter(
        pk=documento_id, contrato=contrato, activo=True,
    ).first()
    if soporte is None:
        raise ValidationError('Seleccione un soporte activo del mismo expediente.')
    return soporte


def _reintento(contrato, usuario, operacion, firma):
    # Se aplica el mismo bloqueo a tareas y eventos para evitar reutilizar un UUID.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [17004431, operacion.int & 0x7FFFFFFF])
    actuacion = ActuacionContractual.objects.filter(operacion=operacion).first()
    evento = EventoActuacionContractual.objects.filter(operacion=operacion).select_related('actuacion').first()
    registro = actuacion or evento
    if registro is None:
        return None
    contrato_id = actuacion.contrato_id if actuacion else evento.actuacion.contrato_id
    if (contrato_id, registro.usuario_id, registro.huella) != (contrato.pk, usuario.pk, firma):
        raise ConflictoEdicion()
    return registro


def _crear_actuacion(*, usuario, contrato, accion, operacion, huella, soporte, datos):
    tarea = accion == 'planificar'
    registro = ActuacionContractual(
        contrato=contrato, usuario=usuario, documento=soporte, operacion=operacion, huella=huella,
        tipo=ActuacionContractual.Tipo.TAREA if tarea else ActuacionContractual.Tipo.MEMORANDO,
        titulo=datos['titulo'], descripcion=datos.get('descripcion', ''),
        fecha=timezone.localdate() if tarea else datos['fecha'],
        vence=datos.get('vence') if tarea else None, radicado='' if tarea else datos['radicado'],
    )
    registro.full_clean(validate_unique=False, validate_constraints=False)
    registro._por_servicio = True
    registro.save(force_insert=True)
    return registro


def _cerrar_tarea(*, usuario, contrato, accion, operacion, huella, soporte, datos):
    tarea = contrato.actuaciones.filter(pk=datos['actuacion_id'], tipo=ActuacionContractual.Tipo.TAREA).first()
    if tarea is None:
        raise Http404('La tarea no pertenece a este expediente.')
    if tarea.eventos.exists():
        raise ConflictoEdicion()
    registro = EventoActuacionContractual(
        actuacion=tarea, usuario=usuario, documento=soporte, operacion=operacion, huella=huella,
        accion=accion, fecha=datos['fecha'], descripcion=datos['descripcion'],
    )
    registro.full_clean(validate_unique=False, validate_constraints=False)
    registro._por_servicio = True
    registro.save(force_insert=True)
    return registro


@transaction.atomic
def registrar_actuacion(*, usuario, contrato_id, accion, operacion, **datos):
    exigir(usuario, 'view_contrato', 'view_historialestadocontrato', 'change_contrato')
    contrato = Contrato.objects.select_for_update().filter(pk=contrato_id, abogado_responsable=usuario).first()
    if contrato is None:
        raise Http404('El expediente no está disponible.')
    if accion not in {'planificar', 'memorando', *EventoActuacionContractual.Accion.values}:
        raise ValidationError('Acción de trabajo desconocida.')
    huella = _firma({'accion': accion, **datos})
    anterior = _reintento(contrato, usuario, operacion, huella)
    if anterior:
        return anterior
    if datos.get('fecha') and datos['fecha'] > timezone.localdate():
        raise ValidationError({'fecha': 'La fecha de una actuación registrada no puede quedar en el futuro.'})
    soporte = _soporte(contrato, usuario, datos.get('documento_id'))
    crear = _crear_actuacion if accion in {'planificar', 'memorando'} else _cerrar_tarea
    registro = crear(usuario=usuario, contrato=contrato, accion=accion, operacion=operacion,
                     huella=huella, soporte=soporte, datos=datos)
    if isinstance(registro, EventoActuacionContractual):
        actuacion = registro.actuacion
    else:
        actuacion = registro
    Auditoria.objects.create(
        usuario=usuario, accion=f'actuacion_{accion}', entidad_afectada=registro.__class__.__name__,
        identificador_registro=str(registro.pk),
        datos_nuevos={'contrato_id': contrato.pk, 'actuacion_id': actuacion.pk, 'registro': valores(registro)},
    )
    return registro
