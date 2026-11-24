"""Context for the first institutional requirement; never advances a contract."""
from hashlib import sha256
import json
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from .calendario import calcular_vencimiento, calendario_disponible
from .docx_requerimientos import leer_docx, procesar_docx
from .models import Contrato, PlantillaDocumento, TipoRequerimiento


def contexto_primer_requerimiento(contrato, plantilla, contenido, marcadores, valores):
    """Only templates requesting tracking data require a live, consistent instance.

    Older generic templates without tracking markers retain their existing behavior.
    No unstructured finding is converted into a financial or legal assertion.
    """
    valores = dict(valores)
    valores.update(tipo_contrato=contrato.tipo_contrato, destinatario=contrato.supervisor_nombre or None,
                   valor_inicial=str(contrato.valor_inicial), valor_registrado=str(contrato.valor_actual))
    atributos = {
        'vigencia':'anio_contractual', 'numero_contrato':'numero_contrato', 'tipo_contrato':'tipo_contrato',
        'entidad':'entidad.nombre', 'nit_entidad':'entidad.nit', 'contratista':'contratista_nombre',
        'identificacion_contratista':'contratista_identificacion', 'objeto':'objeto_contractual',
        'valor_contrato':'valor_actual', 'valor_registrado':'valor_actual', 'valor_inicial':'valor_inicial',
        'fecha_inicio':'fecha_inicio', 'fecha_terminacion':'fecha_terminacion', 'fecha_suscripcion':'fecha_suscripcion',
        'abogado':'abogado_responsable.get_full_name()', 'supervisor':'supervisor_nombre', 'destinatario':'supervisor_nombre',
    }
    fuentes = {campo: f'Contrato #{contrato.pk}.{atributo}; registrado, sujeto a revisión del abogado'
               for campo, atributo in atributos.items()}
    fuentes['fecha_elaboracion'] = 'Fecha local de generación'
    etapa = None
    if marcadores & {'plazo_dias_habiles', 'fecha_vencimiento', 'documentos_solicitados'}:
        etapa = contrato.instancias.filter(fecha_finalizacion__isnull=True).select_related('estado').first()
        if (etapa is None or contrato.estado_actual.codigo != TipoRequerimiento.PRIMERO
                or not contrato.estado_actual.activo or etapa.estado_id != contrato.estado_actual_id):
            raise ValidationError('El primer requerimiento institucional necesita una instancia vigente de Primer requerimiento.')
        if etapa.plazo_dias_habiles != 15 or not etapa.fecha_vencimiento or not etapa.procedencia_calendario:
            raise ValidationError('Revise el plazo y calendario de la instancia: el primer requerimiento requiere 15 días hábiles.')
        try:
            vence, _ = calcular_vencimiento(etapa.fecha_inicio, 15, proveedor=calendario_disponible)
        except (ValueError, OverflowError) as error:
            raise ValidationError('No hay un calendario válido para verificar el vencimiento.') from error
        from .calendarios_servicios import vencimiento_documental
        fecha_documental, revision_calendario = vencimiento_documental(etapa, vence)
        valores.update(plazo_dias_habiles=etapa.plazo_dias_habiles,
                       fecha_vencimiento=fecha_documental)
        fuentes.update(plazo_dias_habiles=f'InstanciaSeguimiento #{etapa.pk}',
                       fecha_vencimiento=f'InstanciaSeguimiento #{etapa.pk}; calendario conservado en la actuación')
        from .revision import resumen_documental
        resumen = resumen_documental(contrato)
        requisitos = [r['tipo'] for r in resumen['requisitos'] if r['estado'] != 'No aplica']
        valores['documentos_solicitados'] = requisitos or None
        fuentes['documentos_solicitados'] = ('Requisitos activos para el tipo contractual, excluye No aplica; verificar antes de emitir'
                                            if resumen['requisitos'] else resumen['aviso'])
    digest = sha256(contenido).hexdigest()
    origen = {'sha256_docx_utilizado': digest, 'plantilla_id': plantilla.pk, 'version': plantilla.version}
    manifiesto = Path(settings.BASE_DIR).parent / 'plantillas_institucionales/v1/inventario.json'
    if manifiesto.exists():
        for ficha in json.loads(manifiesto.read_text(encoding='utf-8')):
            if ficha.get('id') == 1 and ficha.get('sha256_preparada') == digest:
                origen.update(original=ficha['original'], sha256_original=ficha['sha256_original'], version_trabajo=1)
                break
    seguimiento = None if etapa is None else {
        'id': etapa.pk, 'estado': etapa.estado.codigo, 'orden': etapa.orden,
        'inicio': etapa.fecha_inicio.isoformat(), 'vencimiento': etapa.fecha_vencimiento.isoformat(),
        'plazo_dias_habiles': etapa.plazo_dias_habiles, 'procedencia_calendario': etapa.procedencia_calendario,
        'revision_calendario': revision_calendario,
        'vencimiento_efectivo': revision_calendario['vigente'],
    }
    return valores, {
        'procedencia_campos': {campo: fuentes.get(campo, 'Pendiente de registro y validación con soporte; no se deduce del ejemplo') for campo in sorted(marcadores)},
        'actuacion_origen': seguimiento, 'plantilla_origen': origen,
    }


def preparar_primer_requerimiento(*, usuario, contrato_id):
    from .requerimientos import _datos, exigir
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    contrato = Contrato.objects.get(pk=contrato_id)
    plantilla = PlantillaDocumento.objects.filter(tipo=TipoRequerimiento.PRIMERO, activa=True).first()
    if plantilla is None:
        raise ValidationError('Pendiente de plantilla activa de Primer requerimiento.')
    contenido = leer_docx(plantilla.archivo)
    _, usados, _ = procesar_docx(contenido)
    valores, metadatos = contexto_primer_requerimiento(contrato, plantilla, contenido, usados, _datos(contrato))
    return [(campo, valores.get(campo), metadatos['procedencia_campos'][campo]) for campo in sorted(usados)]
