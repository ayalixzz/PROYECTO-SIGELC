from contextlib import contextmanager
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.utils import timezone

from .access import es_abogado_operativo
from .docx_requerimientos import LIMITE, leer_docx, procesar_docx
from .models import Auditoria, Contrato, DocumentoContrato, DocumentoGenerado, PerfilUsuario, PlantillaDocumento, TipoActa, TipoRequerimiento


def exigir(usuario, *permisos):
    if not es_abogado_operativo(usuario) or not usuario.has_perms(
        [f'gestion_contractual.{permiso}' for permiso in permisos]
    ):
        raise PermissionDenied


from .archivos_transaccionales import escritura_con_archivo


def registrar_plantilla(*, usuario, tipo, nombre, archivo, activar, tipo_acta=None, ficticia=False):
    exigir(usuario, 'add_documentocontrato')
    if activar:
        exigir(usuario, 'change_documentocontrato')
    if tipo_acta is not None:
        if not tipo_acta.pk or not tipo_acta.activo or tipo != tipo_acta.clave_documental:
            raise ValidationError('Seleccione un tipo de acta activo.')
    elif tipo not in TipoRequerimiento.values or ficticia:
        raise ValidationError('Indique un tipo documental válido.')
    if not nombre.strip() or len(nombre) > 150:
        raise ValidationError('Indique un tipo de requerimiento válido y un nombre.')
    contenido, marcadores, _ = procesar_docx(leer_docx(archivo))
    if tipo == TipoRequerimiento.GCOF19:
        from .gcof19 import validar_aprobada
        validar_aprobada(contenido)
    if not marcadores:
        raise ValidationError('Incluya al menos un marcador de texto, por ejemplo {{numero_contrato}}.')
    with escritura_con_archivo() as guardar:
        # La cuenta operativa única serializa el versionado del catálogo.
        PerfilUsuario.objects.select_for_update().get(usuario=usuario, abogado_operativo=True)
        if tipo_acta and not TipoActa.objects.select_for_update().filter(pk=tipo_acta.pk, activo=True).exists():
            raise ValidationError('El tipo de acta ya no está activo.')
        versiones = PlantillaDocumento.objects.filter(tipo=tipo)
        anterior = versiones.order_by('-version').first()
        if activar:
            models.QuerySet.update(versiones.filter(activa=True), activa=False)
        plantilla = PlantillaDocumento(nombre=nombre.strip(), tipo=tipo, tipo_acta=tipo_acta, ficticia=ficticia, version=1 if anterior is None else anterior.version + 1,
                                       nombre_original=Path(archivo.name).name, incorporada_por=usuario, activa=activar)
        guardar(plantilla.archivo, plantilla.nombre_original, contenido)
        plantilla.save(force_insert=True)
        Auditoria.objects.create(usuario=usuario, accion='registrar_plantilla_acta' if tipo_acta else 'registrar_plantilla_requerimiento',
                                 entidad_afectada='PlantillaDocumento', identificador_registro=str(plantilla.pk),
                                 datos_nuevos={'tipo': tipo, 'version': plantilla.version, 'activa': activar, 'ficticia': ficticia})
    return plantilla


@transaction.atomic
def cambiar_activacion_plantilla(*, usuario, plantilla_id, activa):
    exigir(usuario, 'change_documentocontrato')
    PerfilUsuario.objects.select_for_update().get(usuario=usuario, abogado_operativo=True)
    plantilla = PlantillaDocumento.objects.get(pk=plantilla_id)
    if activa and plantilla.tipo_acta_id and not TipoActa.objects.select_for_update().filter(pk=plantilla.tipo_acta_id, activo=True).exists():
        raise ValidationError('Active el tipo de acta antes de activar su plantilla.')
    if plantilla.activa == activa:
        return
    if activa:
        modelos_activos = PlantillaDocumento.objects.filter(tipo=plantilla.tipo, activa=True)
        models.QuerySet.update(modelos_activos, activa=False)
    models.QuerySet.update(PlantillaDocumento.objects.filter(pk=plantilla.pk), activa=activa)
    Auditoria.objects.create(usuario=usuario, accion='cambiar_activacion_plantilla',
                             entidad_afectada='PlantillaDocumento', identificador_registro=str(plantilla.pk),
                             datos_anteriores={'activa': plantilla.activa}, datos_nuevos={'activa': activa})


def _datos(contrato):
    abogado = contrato.abogado_responsable
    return {
        'vigencia': contrato.anio_contractual,
        'numero_contrato': contrato.numero_contrato,
        'entidad': contrato.entidad.nombre,
        'nit_entidad': contrato.entidad.nit,
        'contratista': contrato.contratista_nombre,
        'identificacion_contratista': contrato.contratista_identificacion,
        'objeto': contrato.objeto_contractual,
        'valor_contrato': str(contrato.valor_actual),
        'fecha_inicio': contrato.fecha_inicio.isoformat() if contrato.fecha_inicio else None,
        'fecha_terminacion': contrato.fecha_terminacion.isoformat() if contrato.fecha_terminacion else None,
        'fecha_suscripcion': contrato.fecha_suscripcion.isoformat(),
        'abogado': abogado.get_full_name() or None,
        'supervisor': contrato.supervisor_nombre or None,
        'fecha_elaboracion': timezone.localdate().isoformat(),
    }


def generar_requerimiento(*, usuario, contrato_id, tipo, archivo_revision=None, anterior_id=None, operacion=None,
                          tipo_acta_id=None, plantilla_id=None, motivo='', exigir_etapa_vigente=False,
                          revision_confirmada=None):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    es_acta = tipo_acta_id is not None
    if es_acta:
        from .actas import contexto_acta, plantilla_aplicable, rotulo_acta
        acta = TipoActa.objects.filter(pk=tipo_acta_id).first()
        if acta is None or not operacion:
            raise ValidationError('Seleccione el tipo de acta e indique la operación.')
        tipo = acta.clave_documental
    elif tipo not in TipoRequerimiento.values:
        raise ValidationError('Seleccione un tipo de requerimiento válido.')
    if tipo == TipoRequerimiento.GCOF19 and not operacion:
        raise ValidationError('El GCOF19 requiere un identificador de operación para prevenir reintentos duplicados.')
    if (archivo_revision is None) != (anterior_id is None):
        raise ValidationError('La revisión requiere el archivo editado y la versión que se revisó.')
    with escritura_con_archivo() as guardar:
        from .calendarios_servicios import bloquear_calendarios
        bloquear_calendarios()
        contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
        if operacion:
            existente = DocumentoGenerado.objects.filter(operacion=operacion).first()
            if existente:
                if existente.contrato_id != contrato.pk or existente.tipo != tipo or existente.documento.cargado_por_id != usuario.pk:
                    raise ValidationError('Identificador de operación en uso.')
                return existente
        if exigir_etapa_vigente and (contrato.estado_actual.codigo != tipo or not contrato.estado_actual.activo):
            raise ValidationError('Seleccione el requerimiento de la etapa vigente.')
        ultimo = contrato.borradores_requerimiento.filter(tipo=tipo).order_by('-version').first()
        calculo = None
        if archivo_revision is None:
            plantilla = (plantilla_aplicable(acta, plantilla_id) if es_acta else
                         PlantillaDocumento.objects.filter(tipo=tipo, activa=True).first())
            if plantilla is None:
                raise ValidationError('Pendiente de plantilla institucional activa para este tipo documental.')
            valores = _datos(contrato)
            resumen = None
            metadatos_primer = None
            contenido_plantilla = leer_docx(plantilla.archivo)
            if tipo == TipoRequerimiento.GCOF19:
                from .gcof19 import preparar_contexto
                contenido_plantilla, valores, metadatos_primer = preparar_contexto(contrato, plantilla, contenido_plantilla)
            if tipo == TipoRequerimiento.PRIMERO:
                from .primer_requerimiento import contexto_primer_requerimiento
                _, marcadores, _ = procesar_docx(contenido_plantilla)
                valores, metadatos_primer = contexto_primer_requerimiento(
                    contrato, plantilla, contenido_plantilla, marcadores, valores)
            elif tipo in (TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO):
                from .requerimientos_sucesivos import contexto_requerimiento_sucesivo
                _, marcadores, _ = procesar_docx(contenido_plantilla)
                valores, metadatos_primer = contexto_requerimiento_sucesivo(
                    contrato, plantilla, contenido_plantilla, marcadores, valores, usuario=usuario)
            if es_acta:
                valores, procedencia, soportes, calculo = contexto_acta(contrato)
                from .liquidacion import es_institucional, contexto as contexto_liquidacion
                if es_institucional(contenido_plantilla):
                    if not operacion:
                        raise ValidationError('El acta institucional requiere identificador de operación.')
                    contenido_plantilla, valores, procedencia, metadatos_primer, calculo = contexto_liquidacion(contrato,plantilla,contenido_plantilla,usuario=usuario)
            if tipo == TipoRequerimiento.OBSERVACIONES:
                _, marcadores, _ = procesar_docx(contenido_plantilla)
                if 'rotulo_borrador' in marcadores:
                    from .actas_observaciones import contexto_acta_observaciones
                    valores, metadatos_primer = contexto_acta_observaciones(
                        contrato, plantilla, contenido_plantilla, valores, revision_confirmada=revision_confirmada,
                    )
                else:
                    if revision_confirmada is not None:
                        raise ValidationError('La revisión requiere la plantilla institucional de observaciones.')
                    from .revision import preparar_observaciones
                    resumen, campos = preparar_observaciones(contrato)
                    valores.update(campos)
            if revision_confirmada is not None and tipo in (
                TipoRequerimiento.PRIMERO, TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO,
            ):
                _, marcadores, _ = procesar_docx(contenido_plantilla)
                if 'documentos_solicitados' not in marcadores:
                    raise ValidationError('La plantilla activa no admite las observaciones seleccionadas.')
                valores['documentos_solicitados'] = revision_confirmada['textos']
                metadatos_primer['procedencia_campos']['documentos_solicitados'] = 'Selección confirmada por el abogado'
            contenido, usados, pendientes = procesar_docx(contenido_plantilla, valores,
                                                          rotulo=rotulo_acta(plantilla) if es_acta else None)
            datos = {clave: valores.get(clave) for clave in sorted(usados)}
            if revision_confirmada is not None:
                datos['revision_inteligente'] = revision_confirmada
            if metadatos_primer is not None:
                datos.update(metadatos_primer)
                pendientes = sorted(set(pendientes) | set(metadatos_primer.get('pendientes_documentales', [])))
            if es_acta:
                datos = {
                    'campos': datos,
                    'procedencia': {clave: procedencia.get(clave, 'Pendiente de diligenciamiento con soporte') for clave in sorted(usados)},
                    'soportes': soportes,
                }
                if not any(datos['campos'].get(k) and procedencia.get(k, '').startswith('Cálculo jurídico verificado') for k in usados):
                    calculo = None
                if metadatos_primer and metadatos_primer.get('liquidacion_institucional'):
                    datos.update(metadatos_primer)
            if resumen is not None:
                datos['resumen_documental'] = resumen
        else:
            if ultimo is None or ultimo.pk != anterior_id:
                raise ValidationError('Ya existe una versión posterior. Descárguela antes de revisar.')
            if ultimo.calculo_competencia_id:
                from .competencia import calculo_verificado_vigente
                vigente = calculo_verificado_vigente(contrato)
                if not vigente or vigente.pk != ultimo.calculo_competencia_id:
                    raise ValidationError('El DOCX original cita una verificación jurídica que perdió vigencia. Genere un borrador actualizado antes de aportar una revisión; se conserva el documento histórico.')
            origen = ultimo.datos_utilizados.get('actuacion_origen')
            if origen and tipo in (TipoRequerimiento.PRIMERO, TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO):
                from .calendarios_servicios import revisar_instancia
                from .models import InstanciaSeguimiento
                instancia = InstanciaSeguimiento.objects.get(pk=origen['id'],contrato=contrato)
                revision = revisar_instancia(instancia)
                if (revision['clasificacion'] == 'afectado' or (revision['vigente'] and
                        origen.get('vencimiento_efectivo',origen.get('vencimiento')) != revision['vigente'])):
                    raise ValidationError('El borrador original conserva otro vencimiento operativo. Genere y revise un borrador actualizado; no se modifica el histórico.')
            plantilla = ultimo.plantilla
            if es_acta and not motivo.strip():
                raise ValidationError('Indique el motivo de la revisión del acta.')
            contenido, _, _ = procesar_docx(leer_docx(archivo_revision), rotulo=rotulo_acta(plantilla) if es_acta else None)
            datos, pendientes = ultimo.datos_utilizados, ultimo.campos_pendientes
            if datos.get('acta_observaciones_institucional'):
                from .actas_observaciones import validar_revision
                validar_revision(contenido, motivo)
                datos = {**datos, 'revision_manual': 'DOCX aportado por el abogado; instantánea y pendientes de la generación original.'}
            if tipo == TipoRequerimiento.GCOF19:
                from .gcof19 import validar_revision
                validar_revision(contenido, motivo)
                datos = {**datos, 'revision_manual': 'DOCX editado por el abogado; datos y pendientes conservan la instantánea original. Cambios manuales no certificados automáticamente.'}
            if es_acta:
                calculo = ultimo.calculo_competencia
                if datos.get('liquidacion_institucional'):
                    from .liquidacion import validar_revision
                    validar_revision(contenido,motivo)
                datos = {**datos, 'revision_manual': 'DOCX aportado por el abogado. Los campos registrados corresponden a la generación original.'}
        if len(contenido) > LIMITE:
            raise ValidationError('El borrador supera el límite de 20 MiB.')
        version = 1 if ultimo is None else ultimo.version + 1
        nombre = f'borrador-{tipo}-{contrato.pk}-v{version}.docx'
        documento = DocumentoContrato(contrato=contrato, tipo_documento=plantilla.get_tipo_display(),
                                      nombre_original=nombre, cargado_por=usuario,
                                      descripcion=rotulo_acta(plantilla) if es_acta else 'Borrador sujeto a revisión.')
        guardar(documento.archivo, nombre, contenido)
        documento.save(force_insert=True)
        borrador = DocumentoGenerado.objects.create(documento=documento, contrato=contrato, plantilla=plantilla, tipo=tipo,
                                                    version=version, anterior=ultimo, datos_utilizados=datos,
                                                    campos_pendientes=pendientes, operacion=operacion,
                                                    calculo_competencia=calculo, motivo_revision=motivo.strip())
        accion = ('revisar' if archivo_revision else 'generar') + ('_acta' if es_acta else '_requerimiento')
        Auditoria.objects.create(usuario=usuario, accion=accion,
                                 entidad_afectada='DocumentoGenerado', identificador_registro=str(borrador.pk),
                                 datos_nuevos={'contrato_id': contrato.pk, 'plantilla_id': plantilla.pk,
                                               'version_plantilla': plantilla.version, 'version': version, 'pendientes': pendientes,
                                               'calculo_id': borrador.calculo_competencia_id, 'motivo': borrador.motivo_revision})
    return borrador
