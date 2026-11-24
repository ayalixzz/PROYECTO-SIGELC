"""Diligenciamiento institucional del segundo y tercer requerimiento."""
from hashlib import sha256
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from .calendario import calcular_vencimiento, calendario_disponible
from .docx_requerimientos import leer_docx, procesar_docx
from .models import Contrato, PlantillaDocumento, TipoRequerimiento


PLAZOS = {TipoRequerimiento.SEGUNDO: 8, TipoRequerimiento.TERCERO: 5}


def contexto_requerimiento_sucesivo(contrato, plantilla, contenido, marcadores, valores, *, usuario):
    if plantilla.tipo not in PLAZOS:
        raise ValidationError('Seleccione segundo o tercer requerimiento.')
    plazo = PLAZOS[plantilla.tipo]
    etapa = contrato.instancias.filter(fecha_finalizacion__isnull=True).select_related('estado').first()
    if (etapa is None or contrato.estado_actual.codigo != plantilla.tipo
            or etapa.estado_id != contrato.estado_actual_id or not contrato.estado_actual.activo):
        raise ValidationError('El requerimiento seleccionado no corresponde a la instancia vigente del contrato.')
    if etapa.plazo_dias_habiles != plazo or not etapa.fecha_vencimiento or not etapa.procedencia_calendario:
        raise ValidationError(f'La instancia requiere un plazo sustentado de {plazo} días hábiles y su vencimiento.')
    try:
        vence, procedencia_actual = calcular_vencimiento(etapa.fecha_inicio, plazo, proveedor=calendario_disponible)
    except (ValueError, OverflowError) as error:
        raise ValidationError('No hay calendario disponible para verificar el vencimiento.') from error
    from .calendarios_servicios import vencimiento_documental
    fecha_documental, revision_calendario = vencimiento_documental(etapa, vence)

    valores = dict(valores)
    valores.update(tipo_contrato=contrato.tipo_contrato, destinatario=contrato.supervisor_nombre or None,
                   plazo_dias_habiles=etapa.plazo_dias_habiles, fecha_vencimiento=fecha_documental,
                   abogado=usuario.get_full_name() or None)
    # El perfil del abogado no identifica la oficina/cargo del supervisor.
    # Tampoco se atribuye una revisión o aprobación por el hecho de generar un borrador.
    valores.update(dependencia=None, cargo_supervisor=None, revisor=None,
                   consideracion_oportunidad=None, observaciones_pagos=None, firmante=None, anexos=None, copias=None)
    atributos = {
        'numero_contrato': 'numero_contrato', 'vigencia': 'anio_contractual', 'tipo_contrato': 'tipo_contrato',
        'entidad': 'entidad.nombre', 'nit_entidad': 'entidad.nit', 'destinatario': 'supervisor_nombre',
        'supervisor': 'supervisor_nombre', 'contratista': 'contratista_nombre',
        'identificacion_contratista': 'contratista_identificacion', 'objeto': 'objeto_contractual',
        'fecha_suscripcion': 'fecha_suscripcion', 'fecha_inicio': 'fecha_inicio', 'fecha_terminacion': 'fecha_terminacion',
    }
    fuentes = {campo: f'Contrato #{contrato.pk}.{atributo}; dato registrado, sujeto a revisión'
               for campo, atributo in atributos.items()}
    fuentes.update(fecha_elaboracion='Fecha local de generación, zona horaria de SIGELC',
                   abogado=f'Usuario generador #{usuario.pk}.get_full_name(); elaboración, no aprobación',
                   plazo_dias_habiles=f'InstanciaSeguimiento #{etapa.pk}.plazo_dias_habiles',
                   fecha_vencimiento=f'InstanciaSeguimiento #{etapa.pk}.fecha_vencimiento; cotejado con calendario vigente')
    from .revision import resumen_documental
    resumen = resumen_documental(contrato)
    faltantes = [r['tipo'] for r in resumen['requisitos'] if r['estado'] == 'Faltante']
    valores['documentos_solicitados'] = faltantes or None
    fuentes['documentos_solicitados'] = (
        'Requisitos activos aplicables al tipo contractual, clasificados Faltante en el expediente'
        if faltantes else resumen['aviso'] or 'No hay faltantes entre los requisitos configurados; solicitud específica pendiente de validación'
    )
    digest = sha256(contenido).hexdigest()
    origen = {'sha256_docx_utilizado': digest, 'plantilla_id': plantilla.pk, 'version': plantilla.version}
    manifiesto = Path(settings.BASE_DIR).parent / 'plantillas_institucionales/v2/inventario.json'
    if manifiesto.exists():
        for ficha in json.loads(manifiesto.read_text(encoding='utf-8')):
            if ficha['tipo'] == plantilla.tipo and ficha['sha256_preparada'] == digest:
                origen.update(original=ficha['original'], sha256_original=ficha['sha256_original'], version_trabajo=2)
                break
    return valores, {
        'procedencia_campos': {campo: fuentes.get(campo, 'Pendiente de registro específico y validación con soporte; no se deduce del ejemplo') for campo in sorted(marcadores)},
        'actuacion_origen': {'id': etapa.pk, 'orden': etapa.orden, 'estado': etapa.estado.codigo,
                            'inicio': etapa.fecha_inicio.isoformat(), 'vencimiento': etapa.fecha_vencimiento.isoformat(),
                            'vencimiento_efectivo': revision_calendario['vigente'],
                            'plazo_dias_habiles': etapa.plazo_dias_habiles,
                            'procedencia_calendario': etapa.procedencia_calendario,
                            'calendario_cotejado': procedencia_actual},
        'revision_calendario': revision_calendario,
        'vencimiento_efectivo': revision_calendario['vigente'],
        'plantilla_origen': origen,
        'requisitos_evaluados': resumen['requisitos'],
    }


def preparar_requerimiento_sucesivo(*, usuario, contrato_id, tipo):
    from .requerimientos import _datos, exigir
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    if tipo not in PLAZOS:
        raise ValidationError('Seleccione segundo o tercer requerimiento.')
    contrato = Contrato.objects.get(pk=contrato_id)
    plantilla = PlantillaDocumento.objects.filter(tipo=tipo, activa=True).first()
    if plantilla is None:
        raise ValidationError('Pendiente de plantilla institucional activa para esta etapa.')
    contenido = leer_docx(plantilla.archivo)
    _, usados, _ = procesar_docx(contenido)
    valores, metadatos = contexto_requerimiento_sucesivo(contrato, plantilla, contenido, usados, _datos(contrato), usuario=usuario)
    return [(campo, '\n'.join(valores[campo]) if isinstance(valores.get(campo), list) else valores.get(campo),
             metadatos['procedencia_campos'][campo]) for campo in sorted(usados)]
