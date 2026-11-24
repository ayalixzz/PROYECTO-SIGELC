"""Datos de presentación para el recorrido contractual de la demo."""
from django.urls import reverse

from .api_demo_serializers import ContratoSalida
from .calendarios_servicios import revisar_instancia


def estado_datos(estado):
    return {'id': estado.pk, 'codigo': estado.codigo, 'nombre': estado.nombre}


def entidad_datos(entidad):
    return {'id': entidad.pk, 'nombre': entidad.nombre} if entidad else None


def vencimiento_datos(instancia):
    if not instancia or not instancia.fecha_vencimiento:
        return None
    try:
        revision = revisar_instancia(instancia)
    except (ValueError, OverflowError):
        return {
            'fecha': instancia.fecha_vencimiento.isoformat(),
            'fecha_original': instancia.fecha_vencimiento.isoformat(),
            'fecha_teorica': None,
            'estado': 'requiere_revision',
            'advertencia': 'Falta un calendario utilizable para verificar este vencimiento original.',
        }
    if revision['clasificacion'] == 'afectado':
        estado = 'requiere_revision'
        advertencia = 'Cambió el vencimiento teórico. Se conserva el registrado hasta una revisión expresa.'
    elif revision.get('provisional'):
        estado = 'provisional'
        advertencia = 'Calendario sin aprobar para todo el período. El vencimiento es provisional.'
    elif revision['clasificacion'] == 'incierto':
        estado = 'requiere_revision'
        advertencia = 'La procedencia del vencimiento exige revisión.'
    else:
        estado = 'confirmado'
        advertencia = None
    return {
        'fecha': revision['vigente'],
        'fecha_original': revision['historico'],
        'fecha_teorica': revision['teorico'],
        'estado': estado,
        'advertencia': advertencia,
    }


def seguimiento_datos(instancia, vencimiento):
    return {
        'id': instancia.pk,
        'orden': instancia.orden,
        'estado': estado_datos(instancia.estado),
        'fecha_inicio': instancia.fecha_inicio,
        'fecha_finalizacion': instancia.fecha_finalizacion,
        'plazo_dias_habiles': instancia.plazo_dias_habiles,
        'vencimiento_operativo': vencimiento,
    }


def contrato_datos(contrato, vencimiento):
    return {
        **ContratoSalida(contrato).data,
        'entidad': entidad_datos(contrato.entidad),
        'estado_actual': estado_datos(contrato.estado_actual),
        'vencimiento_operativo': vencimiento,
    }


def historial_datos(contrato):
    historial = contrato.historial_estados.select_related('estado_anterior', 'estado_nuevo').order_by('-fecha_solicitud', '-pk')
    return [{
        'id': evento.pk,
        'estado_anterior': estado_datos(evento.estado_anterior),
        'estado_nuevo': estado_datos(evento.estado_nuevo),
        'resultado': evento.resultado,
        'motivo': evento.motivo,
        'observaciones': evento.observaciones,
        'fecha_solicitud': evento.fecha_solicitud,
        'fecha_aprobacion': evento.fecha_aprobacion,
    } for evento in historial]


def documento_datos(documento):
    return {
        'id': documento.pk,
        'tipo_documento': documento.tipo_documento,
        'nombre_original': documento.nombre_original,
        'descripcion': documento.descripcion,
        'fecha_carga': documento.fecha_carga,
        'version': documento.version,
        'estado_revision': documento.get_estado_revision_display(),
        'descarga_url': reverse('sigelc_demo:descargar', args=[documento.contrato_id, documento.pk]) if documento.activo else None,
    }


def borrador_datos(borrador):
    return {
        'id': borrador.pk,
        'version': borrador.version,
        'documento': documento_datos(borrador.documento),
        'campos_pendientes': borrador.campos_pendientes,
    }
