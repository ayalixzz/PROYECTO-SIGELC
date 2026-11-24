"""Registro por versiones; la verificación documental no certifica ejecución."""
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Auditoria, Contrato, DocumentoContrato, HallazgoObligacion, ObligacionContractual, ObservacionDocumental, SoporteObligacion
from .requerimientos import exigir


def vigentes(contrato):
    return contrato.obligaciones.filter(rectificacion__isnull=True).select_related('responsable').prefetch_related('documentos', 'observaciones_abogado').order_by('tipo', 'numero')


def ficha(ob):
    documentos = list(ob.documentos.all())
    observaciones = list(ob.observaciones_abogado.all())
    if any(d.contrato_id != ob.contrato_id for d in documentos) or any(o.contrato_id != ob.contrato_id for o in observaciones):
        raise ValidationError('La obligación tiene referencias de otro contrato.')
    return {'id': ob.pk, 'contrato_id': ob.contrato_id, 'numero': ob.numero, 'tipo': ob.tipo,
            'descripcion': ob.descripcion, 'actividad': ob.actividad, 'producto': ob.producto,
            'comentario_supervisor': ob.comentario_supervisor, 'comentario_verificado': ob.comentario_verificado,
            'porcentaje': str(ob.porcentaje) if ob.porcentaje is not None else None,
            'estado_verificacion': ob.estado_verificacion, 'fundamento': ob.fundamento,
            'responsable_id': ob.responsable_id, 'responsable': ob.responsable.get_username(),
            'registrada_en': ob.registrada_en.isoformat(), 'version': ob.version,
            'anterior_id': ob.anterior_id, 'motivo': ob.motivo,
            'documentos': [{'id': d.pk, 'nombre': d.nombre_original, 'version': d.version,
                            'revision': d.estado_revision} for d in documentos],
            'observaciones_abogado': [{'id': o.pk, 'descripcion': o.descripcion, 'tipo': o.tipo,
                                      'evidencia': o.evidencia, 'revision': o.estado_revision,
                                      'responsable_id': o.responsable_id, 'registrada_en': o.registrada_en.isoformat()}
                                     for o in observaciones]}


@transaction.atomic
def registrar_obligacion(*, usuario, contrato_id, numero, tipo, descripcion, actividad='', producto='',
                         comentario_supervisor='', comentario_verificado=False, porcentaje=None,
                         estado_verificacion='pendiente', fundamento='', documentos=(), observaciones_abogado=(),
                         operacion, anterior=None, motivo=''):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'change_documentocontrato')
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    try:
        operacion = UUID(str(operacion))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('Indique un identificador de operación válido.') from exc
    existente = ObligacionContractual.objects.filter(operacion=operacion).first()
    if existente:
        if existente.contrato_id != contrato.pk or existente.responsable_id != usuario.pk:
            raise ValidationError('Identificador de operación en uso.')
        return existente
    if not descripcion.strip():
        raise ValidationError('Registre la descripción contractual; no se inventan obligaciones.')
    if anterior:
        anterior = ObligacionContractual.objects.filter(pk=anterior.pk, contrato=contrato, rectificacion__isnull=True).first()
        if anterior is None or (anterior.numero, anterior.tipo) != (numero, tipo) or not motivo.strip():
            raise ValidationError('Seleccione la versión vigente del mismo número y tipo e indique el motivo de rectificación.')
    elif contrato.obligaciones.filter(numero=numero, tipo=tipo).exists():
        raise ValidationError('La obligación ya existe; registre una rectificación de su versión vigente.')
    ids_documentos = {d.pk for d in documentos}
    ids_observaciones = {o.pk for o in observaciones_abogado}
    docs = list(DocumentoContrato.objects.filter(pk__in=ids_documentos, contrato=contrato, generacion__isnull=True))
    obs = list(ObservacionDocumental.objects.filter(pk__in=ids_observaciones, contrato=contrato))
    if len(docs) != len(ids_documentos) or len(obs) != len(ids_observaciones):
        raise ValidationError('Todos los soportes y hallazgos deben pertenecer al mismo contrato y ser documentos fuente.')
    if estado_verificacion == 'verificada' and (not docs or not fundamento.strip()):
        raise ValidationError('La verificación exige soporte y referencia documental.')
    if porcentaje is not None and estado_verificacion != 'verificada':
        raise ValidationError('El porcentaje requiere verificación documental; deje el campo vacío si está pendiente.')
    if comentario_verificado and (not comentario_supervisor.strip() or estado_verificacion != 'verificada'):
        raise ValidationError('Para atribuir un comentario al supervisor, verifique su contenido y soporte.')
    ob = ObligacionContractual(contrato=contrato, numero=numero, tipo=tipo, descripcion=descripcion.strip(),
            actividad=actividad.strip(), producto=producto.strip(), comentario_supervisor=comentario_supervisor.strip(),
            comentario_verificado=comentario_verificado, porcentaje=porcentaje, estado_verificacion=estado_verificacion,
            fundamento=fundamento.strip(), responsable=usuario, anterior=anterior,
            version=anterior.version+1 if anterior else 1, motivo=motivo.strip(), operacion=operacion)
    ob.save(force_insert=True)
    for d in docs: SoporteObligacion.objects.create(obligacion=ob, documento=d)
    for o in obs: HallazgoObligacion.objects.create(obligacion=ob, observacion=o)
    Auditoria.objects.create(usuario=usuario, accion='rectificar_obligacion' if anterior else 'registrar_obligacion',
            entidad_afectada='ObligacionContractual', identificador_registro=str(ob.pk),
            datos_anteriores={'anterior_id': anterior.pk} if anterior else {}, datos_nuevos=ficha(ob))
    return ob
