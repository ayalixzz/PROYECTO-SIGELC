from django.core.exceptions import ValidationError

from .competencia import calculo_verificado_vigente
from .docx_requerimientos import leer_docx, procesar_docx
from .models import Contrato, PlantillaDocumento, TipoActa
from .requerimientos import _datos, exigir


ROTULO = 'BORRADOR — PENDIENTE DE REVISIÓN JURÍDICA'


def rotulo_acta(plantilla):
    if not plantilla.ficticia:
        from .liquidacion import es_institucional, ROTULO as ROTULO_LIQUIDACION
        if es_institucional(leer_docx(plantilla.archivo)):
            return ROTULO_LIQUIDACION
    return ROTULO + (' · DOCUMENTO FICTICIO NO INSTITUCIONAL' if plantilla.ficticia else '')


def plantilla_aplicable(tipo_acta, plantilla_id):
    if not plantilla_id:
        raise ValidationError('Seleccione una plantilla activa del tipo de acta.')
    plantilla = PlantillaDocumento.objects.filter(
        pk=plantilla_id, tipo_acta=tipo_acta, tipo_acta__activo=True,
        activa=True, tipo=tipo_acta.clave_documental,
    ).first()
    if plantilla is None:
        raise ValidationError('La plantilla no está disponible para el tipo de acta seleccionado.')
    return plantilla


def contexto_acta(contrato):
    valores = _datos(contrato)
    # Los importes del contrato no acreditan pagos ni un valor final conciliado.
    valores.update(
        objeto_contrato=contrato.objeto_contractual, tipo_contrato=contrato.tipo_contrato,
        modalidad_contratacion=contrato.modalidad_contratacion,
        valor_inicial=str(contrato.valor_inicial), valor_actual_registrado=str(contrato.valor_actual),
    )
    procedencia = {clave: 'Registro contractual' for clave in valores}
    procedencia['valor_contrato'] = 'Valor actual registrado en el contrato, pendiente de verificación financiera'
    procedencia['valor_actual_registrado'] = procedencia['valor_contrato']
    # El contrato carece de una marca de validación financiera y de soporte del total actualizado.
    valores['valor_contrato'] = None
    valores['valor_actual_registrado'] = f'{contrato.valor_actual} (valor registrado, pendiente de verificación financiera)'
    procedencia['fecha_elaboracion'] = 'Fecha del sistema'
    soportes = list(contrato.documentos.filter(activo=True, generacion__isnull=True,
                                             documento_contrato_siguiente__isnull=True).values(
        'id', 'nombre_original', 'version', 'tipo_documento', 'estado_revision',
    ).order_by('id'))
    valores['soportes_documentales'] = '\n'.join(
        f"{d['nombre_original']} (v{d['version']}, #{d['id']})" for d in soportes
    ) or None
    procedencia['soportes_documentales'] = 'Inventario del expediente, sin extracción ni validación de montos'
    calculo = calculo_verificado_vigente(contrato)
    for marcador, clave in {
        'fecha_base_juridica': 'fecha_base',
        'fecha_fin_bilateral': 'fin_bilateral',
        'fecha_fin_unilateral': 'fin_unilateral',
        'fecha_fin_judicial': 'fin_judicial',
        'fecha_limite_competencia': 'limite_competencia',
    }.items():
        valores[marcador] = calculo.resultado.get(clave) if calculo else None
        procedencia[marcador] = f'Cálculo jurídico verificado v{calculo.version}' if calculo else 'Pendiente de verificación jurídica'
    return valores, procedencia, soportes, calculo


def preparar_acta(*, usuario, contrato_id, tipo_acta_id, plantilla_id):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    contrato = Contrato.objects.get(pk=contrato_id)
    tipo = TipoActa.objects.filter(pk=tipo_acta_id, activo=True).first()
    if tipo is None:
        raise ValidationError('Seleccione un tipo de acta activo.')
    plantilla = plantilla_aplicable(tipo, plantilla_id)
    contenido = leer_docx(plantilla.archivo)
    from .liquidacion import es_institucional, contexto
    if es_institucional(contenido):
        _, valores, procedencia, metadata, _ = contexto(contrato, plantilla, contenido,usuario=usuario)
        return [(k,valores.get(k),procedencia[k]) for k in sorted(procedencia)]
    _, marcadores, _ = procesar_docx(contenido)
    valores, procedencia, _, _ = contexto_acta(contrato)
    return [
        (campo, valores.get(campo), procedencia.get(campo, 'Diligenciamiento manual con soporte'))
        for campo in sorted(marcadores)
    ]
