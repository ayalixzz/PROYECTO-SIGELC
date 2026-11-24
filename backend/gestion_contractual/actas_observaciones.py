"""Acta institucional: instantánea atribuida, sin inferencias de responsabilidad."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from django.core.exceptions import ValidationError
from django.db import transaction

from .docx_requerimientos import leer_docx, procesar_docx
from .models import Contrato, ObservacionDocumental, PlantillaDocumento, TipoRequerimiento
from .revision import documentos_vigentes, resumen_documental

ROTULO = 'BORRADOR — PENDIENTE DE REVISIÓN'


def validar_revision(contenido, motivo):
    if not motivo.strip():
        raise ValidationError('Indique el motivo de la revisión del acta de observaciones.')
    with ZipFile(BytesIO(contenido)) as paquete:
        root = ET.fromstring(paquete.read('word/document.xml'))
    texto = ''.join(root.itertext())
    if ROTULO not in texto:
        raise ValidationError('Conserve el rótulo de borrador pendiente de revisión en el acta.')


def referencia_documento(doc):
    return {'id': doc.pk, 'contrato_id': doc.contrato_id, 'nombre': doc.nombre_original,
            'version': doc.version, 'revision': doc.estado_revision,
            'categoria_id': doc.categoria_id, 'responsable_id': doc.cargado_por_id,
            'fecha_carga': doc.fecha_carga.isoformat(),
            'fecha_recepcion': doc.fecha_recepcion.isoformat() if doc.fecha_recepcion else None}


def instantanea(contrato):
    registros = list(ObservacionDocumental.objects.filter(contrato=contrato)
                     .select_related('responsable').prefetch_related('documentos').order_by('pk'))
    por_id = {o.pk: o for o in registros}
    reemplazadas = {o.anterior_id for o in registros if o.anterior_id}

    def ficha(o):
        docs = list(o.documentos.all())
        if any(d.contrato_id != contrato.pk for d in docs):
            raise ValidationError('Una observación contiene un soporte de otro contrato.')
        return {'id': o.pk, 'descripcion': o.descripcion, 'tipo': o.tipo,
                'tipo_nombre': o.get_tipo_display(), 'revision': o.estado_revision,
                'revision_nombre': o.get_estado_revision_display(), 'evidencia': o.evidencia,
                'fecha_registro': o.registrada_en.isoformat(), 'responsable_id': o.responsable_id,
                'responsable': o.responsable.get_full_name() or o.responsable.get_username(),
                'anterior_id': o.anterior_id, 'motivo': o.motivo,
                'documentos': [referencia_documento(d) for d in docs]}

    fichas = {o.pk: ficha(o) for o in registros}
    vigentes = []
    for o in registros:
        if o.anterior_id and o.anterior_id not in por_id:
            raise ValidationError('Rectificación de otro contrato o relación incompleta.')
        if o.pk in reemplazadas:
            continue
        cadena, vistos, nodo = [], set(), o
        while nodo is not None:
            if nodo.pk in vistos:
                raise ValidationError('Ciclo en el historial de rectificaciones.')
            vistos.add(nodo.pk)
            cadena.append(fichas[nodo.pk])
            nodo = por_id.get(nodo.anterior_id)
        vigentes.append({**fichas[o.pk], 'historial_rectificaciones': list(reversed(cadena))})
    # Un ciclo sin hojas tampoco puede desaparecer silenciosamente del acta.
    alcanzados = {f['id'] for o in vigentes for f in o['historial_rectificaciones']}
    if alcanzados != set(por_id):
        raise ValidationError('Historial de observaciones inconsistente.')
    return {'contrato_id': contrato.pk, 'observaciones': vigentes,
            'documentos': [referencia_documento(d) for d in documentos_vigentes(contrato).order_by('pk')],
            'resumen_documental': resumen_documental(contrato)}


def _ref(d):
    return f"#{d['id']} · {d['nombre']} · v{d['version']} · revisión: {d['revision']}"


def contexto_acta_observaciones(contrato, plantilla, contenido, valores, *, revision_confirmada=None):
    snap = instantanea(contrato)
    if revision_confirmada is not None:
        ids = set(revision_confirmada['observaciones_ids'])
        snap['observaciones'] = [o for o in snap['observaciones'] if o['id'] in ids]
        if {o['id'] for o in snap['observaciones']} != ids:
            raise ValidationError('Una observación seleccionada fue rectificada. Revise la selección.')
    obs = snap['observaciones']
    textos = []
    for o in obs:
        referencias = '; '.join(_ref(d) for d in o['documentos']) or '[PENDIENTE: soporte asociado]'
        condicion = ('Anotación registrada por el responsable; pendiente de revisión del acta.'
                     if o['evidencia'].strip() and o['revision'] in ('observado', 'conforme')
                     else 'ANOTACIÓN PENDIENTE DE VALIDACIÓN O SOPORTE.')
        historia = ' → '.join(f"#{h['id']} ({h['revision']})" for h in o['historial_rectificaciones'])
        texto = (f"Observación #{o['id']} · {o['tipo_nombre']} · {o['revision_nombre']}\n"
                      f"{condicion}\n{o['descripcion']}\n"
                      f"Evidencia registrada: {o['evidencia'] or '[PENDIENTE: evidencia]'}\n"
                      f"Soportes: {referencias}\nRegistro: {o['fecha_registro']} · Responsable: {o['responsable']}\n"
                      f"Rectificaciones: {historia}" + (f" · Motivo: {o['motivo']}" if o['motivo'] else ''))
        textos.extend(texto.splitlines())
        textos.append(' ')
    resumen = snap['resumen_documental']
    detectados = [r['tipo'] for r in resumen['requisitos'] if r['estado'] == 'Faltante']
    # Ausencia en el catálogo no equivale a confirmación. Solo reproducir anotaciones
    # explícitas revisadas y con evidencia, sin asociarlas por coincidencia de texto.
    confirmados = [o for o in obs if o['tipo'] == 'faltante' and o['revision'] in ('observado', 'conforme') and o['evidencia'].strip()]
    soportes = {d['id']: d for o in obs for d in o['documentos'] if d['revision'] in ('observado', 'conforme')}
    valores = {**valores, 'rotulo_borrador': ROTULO, 'dependencia': None,
               'fecha_revision': None, 'memorando_radicado': None, 'plazo_ajustes': None,
               'resumen_observaciones': f'{len(obs)} anotaciones vigentes',
               'documentos_recibidos': 'DOCUMENTOS RECIBIDOS\n' + ('\n'.join(_ref(d) for d in snap['documentos']) or '[PENDIENTE: documentos recibidos]'),
               'requisitos_faltantes': 'REQUISITOS POR CONFIRMAR\n' + (resumen['aviso'] or ('; '.join(detectados) + ' (ausencia detectada; requiere confirmación del abogado)' if detectados else 'Sin ausencias detectadas en el catálogo aplicable; no certifica completitud.')),
               'faltantes_confirmados': 'FALTANTES SEGÚN ANOTACIONES REVISADAS CON EVIDENCIA\n' + ('; '.join(f"#{o['id']}: {o['descripcion']}" for o in confirmados) or '[PENDIENTE: confirmación de faltantes]'),
               'observaciones_documentales': textos or ['[PENDIENTE: observaciones del abogado]'],
               'soportes_revisados': 'REFERENCIAS A SOPORTES REVISADOS\n' + ('\n'.join(_ref(d) for d in soportes.values()) or '[PENDIENTE: soportes revisados]')}
    for campo in ('documentos_recibidos', 'requisitos_faltantes', 'faltantes_confirmados', 'soportes_revisados'):
        valores[campo] = valores[campo].splitlines()
    pendientes = ['fecha_revision', 'dependencia', 'memorando_radicado', 'plazo_ajustes', 'comunicacion_devolucion_firmas']
    for nombre, falta in [('documentos_recibidos', not snap['documentos']),
                          ('observaciones_documentales', not obs), ('faltantes_confirmados', not confirmados),
                          ('soportes_revisados', not soportes)]:
        if falta: pendientes.append(nombre)
    if any(not o['evidencia'].strip() or not o['documentos'] or o['revision'] in ('pendiente', 'en_revision') for o in obs):
        pendientes.append('validacion_soportes_observaciones')
    procedencia = {c: f'Contrato.{c}' for c in ('numero_contrato', 'objeto', 'supervisor', 'valor_contrato', 'vigencia', 'abogado')}
    procedencia.update(numero_contrato='Contrato.numero_contrato', objeto='Contrato.objeto_contractual',
        supervisor='Contrato.supervisor_nombre', valor_contrato='Contrato.valor_actual; no certifica pagos',
        vigencia='Contrato.anio_contractual', abogado='Contrato.abogado_responsable; nombre registrado',
        entidad='Contrato.entidad.nombre', rotulo_borrador='Rótulo obligatorio del borrador institucional',
        documentos_recibidos='DocumentoContrato: activos, última versión, sin documentos generados',
        observaciones_documentales='ObservacionDocumental: últimas rectificaciones, con referencias y trazabilidad',
        requisitos_faltantes='RequisitoDocumental activo y aplicable: ausencia detectada, sin confirmar',
        faltantes_confirmados='Anotaciones faltante revisadas (observado/conforme) y con evidencia registrada',
        soportes_revisados='Documentos asociados a las observaciones vigentes, revisados observado/conforme',
        resumen_observaciones='Cantidad de observaciones vigentes del contrato')
    for c in ('fecha_revision', 'dependencia', 'memorando_radicado', 'plazo_ajustes'):
        procedencia[c] = 'PENDIENTE: no hay un dato estructurado confirmado; diligenciamiento por el abogado'
    if revision_confirmada is not None:
        valores['fecha_revision'] = revision_confirmada['fecha']
        procedencia['fecha_revision'] = 'Fecha del paquete documental seleccionado'
        pendientes.remove('fecha_revision')
    metadata = {'acta_observaciones_institucional': True, 'expediente_observaciones': snap,
                'procedencia_observaciones': procedencia,
                'plantilla_origen_observaciones': {'id': plantilla.pk, 'version': plantilla.version, 'sha256': sha256(contenido).hexdigest()},
                'pendientes_documentales': pendientes}
    return valores, metadata


@transaction.atomic
def preparar_acta_observaciones(*, usuario, contrato_id):
    from .requerimientos import exigir, _datos
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    plantilla = PlantillaDocumento.objects.filter(tipo=TipoRequerimiento.OBSERVACIONES, activa=True).first()
    if plantilla is None:
        raise ValidationError('Pendiente de plantilla institucional activa para observaciones.')
    contenido = leer_docx(plantilla.archivo)
    _, marcadores, _ = procesar_docx(contenido)
    if 'rotulo_borrador' not in marcadores:
        raise ValidationError('La plantilla activa no corresponde al acta institucional de observaciones.')
    valores, meta = contexto_acta_observaciones(contrato, plantilla, contenido, _datos(contrato))
    return [(c, valores.get(c), meta['procedencia_observaciones'].get(c, 'Pendiente de confirmar procedencia'))
            for c in sorted(marcadores)], meta['expediente_observaciones']
