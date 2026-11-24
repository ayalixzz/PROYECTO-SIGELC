"""Preparación documental GCOF19; nunca certifica ni cambia estados."""
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as E

from django.core.exceptions import ValidationError
from django.db import transaction

from .docx_requerimientos import leer_docx, procesar_docx
from .gcof19_docx import tablas_obligaciones, validar_plantilla
from .models import Contrato, PlantillaDocumento
from .obligaciones import ficha, vigentes

TIPO = 'gcof19'
ROTULO = 'BORRADOR — PENDIENTE DE REVISIÓN Y CERTIFICACIÓN POR EL RESPONSABLE COMPETENTE.'
SHA_APROBADA = '29e93c706ea456835abd4331f03ca3b7a0e62126e1bc2965ff3ab56b4df24fb7'


def validar_aprobada(contenido):
    validar_plantilla(contenido)
    if sha256(contenido).hexdigest() != SHA_APROBADA:
        raise ValidationError('El archivo GCOF19 no coincide con la plantilla aprobada. No se reconstruye otra versión automáticamente.')


def preparar_contexto(contrato, plantilla, contenido):
    from .requerimientos import _datos
    validar_aprobada(contenido)
    _, marcadores, _ = procesar_docx(contenido)
    valores = {k: '[PENDIENTE]' for k in marcadores}
    base = _datos(contrato)
    fuentes = {}
    for k in ('numero_contrato', 'vigencia', 'contratista', 'supervisor', 'objeto', 'fecha_inicio', 'fecha_terminacion'):
        if base.get(k) is not None and base[k] != '':
            valores[k] = base[k];fuentes[k] = 'Registro contractual SIGELC'
    valores['objeto'] = f'{contrato.objeto_contractual}\nTipo contractual: {contrato.tipo_contrato}'
    fuentes['objeto'] = 'Contrato.objeto_contractual y tipo_contrato, ambos registrados'
    valores['rotulo_gcof19'] = ROTULO
    valores['valor_inicial'] = f'{contrato.valor_inicial} (valor registrado; pendiente de verificación financiera)'
    fuentes['valor_inicial'] = 'Contrato.valor_inicial; no acredita pagos ni saldo'
    actuales = list(vigentes(contrato))
    obligaciones = [ficha(ob) for ob in actuales]
    pendientes = {k for k,v in valores.items() if v == '[PENDIENTE]'}
    # Los marcadores de muestra se sustituyen por filas dinámicas; nunca se rellenan con datos del ejemplo.
    estaticos_ob = {f'{clave}{i}' for clave in ('n','d','a','pr','o','pc') for i in (1,2,3)} | {'grupo_obligaciones'}
    pendientes -= estaticos_ob
    lineas_soporte, lineas_adjuntos = [], {}
    for ob in obligaciones:
        if ob['estado_verificacion'] == 'verificada' and (not ob['documentos'] or not ob['fundamento'].strip()):
            raise ValidationError('Una obligación verificada carece de soporte o fundamento documental.')
        prefix = f"ob_{ob['id']}_"
        for alias,campo in [('n','numero'),('d','descripcion'),('a','actividad'),('pr','producto')]:
            valor = ob[campo]
            valores[prefix+alias] = valor if valor != '' else '[PENDIENTE]'
            if valor == '': pendientes.add(prefix+campo)
        if ob['porcentaje'] is not None:
            if ob['estado_verificacion'] != 'verificada':
                raise ValidationError('El porcentaje no tiene verificación documental.')
            valores[prefix+'pc'] = ob['porcentaje'].replace('.', ',') + ' %'
        else:
            valores[prefix+'pc'] = '[PENDIENTE]';pendientes.add(prefix+'porcentaje')
        if ob['comentario_supervisor'] and ob['comentario_verificado'] and ob['estado_verificacion'] == 'verificada':
            valores[prefix+'o'] = ob['comentario_supervisor']
        else:
            valores[prefix+'o'] = '[PENDIENTE: comentario verificado del supervisor]'
            pendientes.add(prefix+'comentario_supervisor')
        if ob['estado_verificacion'] != 'verificada': pendientes.add(prefix+'validacion')
        documentos = ob['documentos']
        refs = '; '.join(f"#{d['id']} {d['nombre']} v{d['version']}" for d in documentos) or '[PENDIENTE: soportes]'
        if not documentos: pendientes.add(prefix+'soportes')
        label = f"{ob['tipo']} {ob['numero']} · v{ob['version']}"
        lineas_soporte.append(f"{label}: {refs}. Verificación: {ob['estado_verificacion']}. Referencia: {ob['fundamento'] or '[PENDIENTE]'}. Los datos informados no constituyen certificación del supervisor.")
        if ob['comentario_supervisor'] and not ob['comentario_verificado']:
            # Visible separadamente: no atribuir al supervisor en su columna hasta verificar.
            lineas_soporte.append(f"{label}: anotación aportada pendiente de confirmar su atribución al supervisor: {ob['comentario_supervisor']}")
        for o in ob['observaciones_abogado']:
            lineas_soporte.append(f"{label}: referencia a hallazgo del abogado #{o['id']} ({o['revision']}): {o['descripcion']}. Evidencia registrada: {o['evidencia'] or '[PENDIENTE]'}. No es un comentario del supervisor.")
        for d in documentos: lineas_adjuntos[d['id']] = f"#{d['id']} {d['nombre']} v{d['version']} (referencia; archivo no incrustado)"
    valores['soportes_obligaciones'] = lineas_soporte or ['[PENDIENTE: obligaciones y soportes]']
    if lineas_soporte: pendientes.discard('soportes_obligaciones')
    if lineas_adjuntos:
        valores['documentos_adjuntos'] = list(lineas_adjuntos.values());pendientes.discard('documentos_adjuntos')
    if not obligaciones: pendientes.add('obligaciones')
    for k in valores:
        fuentes.setdefault(k, 'Obligaciones y referencias registradas' if k.startswith('ob_') or k in ('soportes_obligaciones','documentos_adjuntos') else 'Pendiente de información y validación por el responsable competente')
    fuentes['rotulo_gcof19'] = 'Rótulo obligatorio de borrador'
    cuerpo = tablas_obligaciones(contenido, obligaciones)
    _, usados, _ = procesar_docx(cuerpo)
    datos = {k: valores[k] for k in sorted(usados)}
    datos.update(gcof19=True, obligaciones=obligaciones,
                 contrato_origen={'id':contrato.pk, 'tipo_contractual':contrato.tipo_contrato,
                                  'entidad_id':contrato.entidad_id, 'numero':contrato.numero_contrato, 'vigencia':contrato.anio_contractual},
                 plantilla_origen={'id':plantilla.pk,'version':plantilla.version,'sha256':sha256(contenido).hexdigest()},
                 procedencia={k:fuentes[k] for k in usados}, pendientes_documentales=sorted(pendientes))
    return cuerpo, valores, datos


@transaction.atomic
def preparar_gcof19(*, usuario, contrato_id):
    from .requerimientos import exigir
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    plantilla = PlantillaDocumento.objects.filter(tipo=TIPO, activa=True).first()
    if plantilla is None: raise ValidationError('Pendiente de incorporar una plantilla GCOF19 aprobada y activa.')
    _, _, datos = preparar_contexto(contrato, plantilla, leer_docx(plantilla.archivo))
    campos = [(k,datos[k],datos['procedencia'][k]) for k in sorted(datos['procedencia'])]
    return campos, datos


def validar_revision(contenido, motivo):
    if not motivo.strip(): raise ValidationError('Indique el motivo de revisión del GCOF19.')
    with ZipFile(BytesIO(contenido)) as z:
        root = E.fromstring(z.read('word/document.xml'))
    if ROTULO not in ''.join(root.itertext()):
        raise ValidationError('Conserve el rótulo de borrador GCOF19 pendiente de revisión y certificación.')
