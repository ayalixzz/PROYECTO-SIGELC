from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.utils import timezone

from .models import Auditoria, Contrato, DocumentoContrato, EstadoRevision, ObservacionDocumental, RequisitoDocumental, SoporteObservacion, TipoDocumental, validar_tamano
from .requerimientos import exigir, escritura_con_archivo


SIN_REQUISITOS = 'Requisitos documentales pendientes de configuración institucional'


def documentos_vigentes(contrato):
    return contrato.documentos.filter(activo=True, documento_contrato_siguiente__isnull=True, generacion__isnull=True)


def resumen_documental(contrato):
    requisitos = RequisitoDocumental.objects.filter(activo=True, tipo__activo=True).exclude(
        clase_expediente=''
    ).filter(clase_expediente__iexact=contrato.tipo_contrato.strip()).select_related('tipo').order_by('tipo__nombre')
    documentos = list(documentos_vigentes(contrato).select_related('categoria').order_by('pk'))
    filas = []
    for requisito in requisitos:
        estados = {d.estado_revision for d in documentos if d.categoria_id == requisito.tipo_id}
        clasificacion = 'Faltante'
        if estados & {EstadoRevision.CONFORME, EstadoRevision.OBSERVADO}:
            clasificacion = 'Aportado'
        elif estados & {EstadoRevision.PENDIENTE, EstadoRevision.EN_REVISION}:
            clasificacion = 'Pendiente de revisión'
        elif EstadoRevision.NO_APLICA in estados:
            clasificacion = 'No aplica'
        filas.append({'tipo': requisito.tipo.nombre, 'estado': clasificacion})
    observaciones = contrato.observaciones_documentales.filter(rectificacion__isnull=True).prefetch_related('documentos').order_by('pk')
    return {
        'requisitos': filas,
        'aviso': '' if filas else SIN_REQUISITOS,
        'documentos': [{'id': d.pk, 'nombre': d.nombre_original, 'version': d.version,
                        'revision': d.get_estado_revision_display()} for d in documentos],
        'observaciones': [{'id': o.pk, 'tipo': o.get_tipo_display(), 'descripcion': o.descripcion,
                           'evidencia': o.evidencia, 'revision': o.get_estado_revision_display(),
                           'documentos': [d.pk for d in o.documentos.all()]} for o in observaciones],
    }


def _auditar(usuario, accion, objeto, datos, anteriores=None):
    Auditoria.objects.create(usuario=usuario, accion=accion, entidad_afectada=type(objeto).__name__,
                             identificador_registro=str(objeto.pk), datos_nuevos=datos,
                             datos_anteriores=anteriores or {})


def recibir_documento(*, usuario, contrato_id, categoria, archivo, fecha_recepcion, descripcion, operacion, anterior=None, motivo=''):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    operacion = UUID(str(operacion))
    FileExtensionValidator(['pdf', 'docx', 'xlsx'])(archivo)
    validar_tamano(archivo)
    if fecha_recepcion and fecha_recepcion > timezone.localdate():
        raise ValidationError('La fecha de recepción no puede ser futura.')
    if anterior and not motivo.strip():
        raise ValidationError('Indique el motivo de rectificación documental.')
    with escritura_con_archivo() as guardar:
        contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
        existente = DocumentoContrato.objects.filter(operacion_recepcion=operacion).first()
        if existente:
            if existente.contrato_id != contrato.pk or existente.cargado_por_id != usuario.pk:
                raise ValidationError('Identificador de operación en uso.')
            return existente
        if not TipoDocumental.objects.filter(pk=categoria.pk, activo=True).exists():
            raise ValidationError('Seleccione un tipo documental activo.')
        if anterior is not None and not documentos_vigentes(contrato).filter(pk=anterior.pk, categoria=categoria).exists():
            raise ValidationError('Seleccione la última versión de este contrato y categoría.')
        documento = DocumentoContrato(
            contrato=contrato, categoria=categoria, tipo_documento=categoria.nombre,
            nombre_original=Path(archivo.name).name, fecha_recepcion=fecha_recepcion,
            descripcion=descripcion, cargado_por=usuario, anterior=anterior, motivo_rectificacion=motivo.strip(),
            version=anterior.version + 1 if anterior else 1, operacion_recepcion=operacion,
        )
        with archivo.open('rb') as entrada:
            guardar(documento.archivo, documento.nombre_original, entrada.read())
        documento.save(force_insert=True)
        _auditar(usuario, 'recibir_documento', documento, {'version': documento.version, 'anterior_id': documento.anterior_id, 'motivo': motivo.strip(), 'fuente_anterior': {'id': anterior.pk, 'version': anterior.version, 'archivo': anterior.archivo.name} if anterior else None})
        return documento


@transaction.atomic
def revisar_documento(*, usuario, contrato_id, documento, estado, estado_esperado):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'change_documentocontrato')
    Contrato.objects.select_for_update().get(pk=contrato_id)
    actual = DocumentoContrato.objects.filter(pk=documento.pk, contrato_id=contrato_id, generacion__isnull=True).first()
    if actual is None or estado not in EstadoRevision.values:
        raise ValidationError('Seleccione un soporte del contrato y un estado válido.')
    if actual.estado_revision == estado:
        return
    if actual.estado_revision != estado_esperado:
        raise ValidationError('La revisión cambió. Recargue antes de guardar.')
    previo = actual.estado_revision
    actual.estado_revision = estado
    actual._revision_autorizada = True
    actual.save(update_fields=['estado_revision'])
    _auditar(usuario, 'revisar_documento', actual, {'estado': estado}, {'estado': previo})


@transaction.atomic
def registrar_observacion(*, usuario, contrato_id, descripcion, tipo, estado_revision, evidencia, documentos, operacion,
                         anterior=None, motivo=''):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'change_documentocontrato')
    operacion = UUID(str(operacion))
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    existente = ObservacionDocumental.objects.filter(operacion=operacion).first()
    if existente:
        if existente.contrato_id != contrato.pk or existente.responsable_id != usuario.pk:
            raise ValidationError('Identificador de operación en uso.')
        return existente
    if anterior and (anterior.contrato_id != contrato.pk or ObservacionDocumental.objects.filter(anterior=anterior).exists() or not motivo.strip()):
        raise ValidationError('Rectifique la última versión del contrato e indique el motivo.')
    if not descripcion.strip():
        raise ValidationError('Describa el hallazgo.')
    ids = {d.pk for d in documentos}
    soportes = list(contrato.documentos.filter(pk__in=ids))
    if len(soportes) != len(ids):
        raise ValidationError('Los soportes deben pertenecer al mismo contrato.')
    observacion = ObservacionDocumental(
        contrato=contrato, descripcion=descripcion.strip(), tipo=tipo, estado_revision=estado_revision,
        evidencia=evidencia.strip(), responsable=usuario, anterior=anterior, motivo=motivo.strip(), operacion=operacion,
    )
    observacion.full_clean()
    observacion.save(force_insert=True)
    for documento in soportes:
        SoporteObservacion.objects.create(observacion=observacion, documento=documento)
    _auditar(usuario, 'rectificar_observacion' if anterior else 'registrar_observacion', observacion,
             {'contrato_id': contrato.pk, 'anterior_id': observacion.anterior_id, 'documentos_ids': sorted(ids)})
    return observacion


def preparar_observaciones(contrato):
    resumen = resumen_documental(contrato)
    valores = {
        'documentos_recibidos': '\n'.join(f"{d['nombre']} (v{d['version']}) — {d['revision']}" for d in resumen['documentos']) or 'Sin soportes registrados.',
        'requisitos_faltantes': resumen['aviso'] or '\n'.join(r['tipo'] for r in resumen['requisitos'] if r['estado'] == 'Faltante') or 'Sin faltantes entre los requisitos configurados.',
        'observaciones_documentales': '\n'.join(
            f"{o['tipo']} ({o['revision']}): {o['descripcion']}"
            + (f" — Evidencia: {o['evidencia']}" if o['evidencia'] else '')
            + (f" — Soportes: {', '.join(map(str, o['documentos']))}" if o['documentos'] else '')
            for o in resumen['observaciones']
        ) or 'Sin observaciones registradas.',
    }
    return resumen, valores
