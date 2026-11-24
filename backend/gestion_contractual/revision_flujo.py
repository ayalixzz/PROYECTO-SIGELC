"""Workflow for immutable package revisions, human decisions and Word drafts."""
from hashlib import sha256
import json
from uuid import UUID, uuid5

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .archivos_transaccionales import escritura_con_archivo
from .calendarios_servicios import bloquear_calendarios, revisar_instancia
from .competencia import calculo_verificado_vigente, fecha_confirmada
from .liquidacion_datos import panorama
from .models import (
    Auditoria, Contrato, DocumentoContrato, EstadoRevision, EventoRevisionInteligente,
    FuenteRevisionInteligente, ObservacionDocumental, RevisionInteligente, TipoRequerimiento,
)
from .obligaciones import ficha, vigentes
from .requerimientos import exigir, generar_requerimiento
from .revision import registrar_observacion, resumen_documental


ETAPAS = {TipoRequerimiento.PRIMERO: 15, TipoRequerimiento.SEGUNDO: 8, TipoRequerimiento.TERCERO: 5}


def huella(datos):
    return sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _contrato(usuario, contrato_id, permiso):
    exigir(usuario, 'view_contrato', 'view_documentocontrato', permiso)
    contrato = Contrato.objects.select_for_update().filter(pk=contrato_id, abogado_responsable=usuario).first()
    if contrato is None:
        from django.http import Http404
        raise Http404
    return contrato


def _estado_actual(contrato):
    return contrato.instancias.filter(fecha_finalizacion__isnull=True).first()


def _auditar(usuario, accion, revision, datos):
    Auditoria.objects.create(
        usuario=usuario, accion=accion, entidad_afectada='RevisionInteligente',
        identificador_registro=str(revision.pk), datos_nuevos=datos,
    )


def _bloquear_operacion(operacion):
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', [17004412, operacion.int & 0x7FFFFFFF])


def _preexistente(revision, usuario, tipo, operacion, firma):
    _bloquear_operacion(operacion)
    evento = EventoRevisionInteligente.objects.filter(operacion=operacion).first()
    if evento and (evento.revision_id, evento.usuario_id, evento.tipo, evento.huella) != (
        revision.pk, usuario.pk, tipo, firma,
    ):
        raise ValidationError('El identificador corresponde a otra operación.')
    return evento


def _evento(revision, usuario, tipo, operacion, firma, datos):
    evento = EventoRevisionInteligente.objects.create(
        revision=revision, usuario=usuario, tipo=tipo, operacion=operacion, huella=firma, datos=datos,
    )
    _auditar(usuario, f'revision_{tipo}', revision, {'evento_id': evento.pk, 'operacion': str(operacion)})
    return evento


def registrar_paquete(*, usuario, contrato_id, archivos, operacion, anterior_id=None):
    from .revision_ingesta import recibir_paquete

    exigir(usuario, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    operacion = UUID(str(operacion))
    extraidos = recibir_paquete(archivos)
    firma = huella([anterior_id, [(a.nombre, a.sha256, a.tipo, a.procedencia) for a in extraidos]])
    with escritura_con_archivo() as guardar:
        contrato = _contrato(usuario, contrato_id, 'add_documentocontrato')
        _bloquear_operacion(operacion)
        repetida = RevisionInteligente.objects.filter(operacion=operacion).first()
        if repetida:
            if (repetida.contrato_id, repetida.usuario_id, repetida.huella) != (contrato.pk, usuario.pk, firma):
                raise ValidationError('El identificador corresponde a otro paquete.')
            return repetida
        anterior = contrato.revisiones_inteligentes.first()
        if (anterior.pk if anterior else None) != anterior_id:
            raise ValidationError('La última entrega cambió. Recargue el expediente.')
        revision = RevisionInteligente.objects.create(
            contrato=contrato, usuario=usuario, version=(anterior.version + 1 if anterior else 1),
            anterior=anterior, etapa=contrato.estado_actual.codigo, instancia=_estado_actual(contrato),
            operacion=operacion, huella=firma,
        )
        for fuente in extraidos:
            registro = FuenteRevisionInteligente(
                revision=revision, nombre=fuente.nombre, sha256=fuente.sha256, tipo=fuente.tipo,
                tamano=len(fuente.contenido), clasificacion=fuente.clasificacion,
                fragmentos=fuente.fragmentos, advertencias=fuente.advertencias, procedencia=fuente.procedencia,
            )
            guardar(registro.archivo, registro.nombre, fuente.contenido)
            registro.save(force_insert=True)
        _auditar(usuario, 'revision_recepcion', revision, {'version': revision.version, 'fuentes': revision.fuentes_revision.count()})
        return revision


def documentos_analizables(revision):
    return list(revision.fuentes_revision.exclude(tipo='zip').values(
        'id', 'nombre', 'sha256', 'tipo', 'clasificacion', 'fragmentos', 'advertencias', 'procedencia',
    ))


def analisis_registrado(revision, *, incluir_mcp=True):
    local = _analisis_con_proveedor(revision)
    if local is None or not incluir_mcp:
        return local
    propuestas = [
        hallazgo for evento in revision.eventos_revision.filter(tipo='mcp_propuestas')
        for hallazgo in evento.datos['hallazgos']
    ]
    return {**local, 'hallazgos': [*local['hallazgos'], *propuestas]} if propuestas else local


def _analisis_con_proveedor(revision):
    evento = revision.eventos_revision.filter(tipo='analisis').first()
    if evento is None:
        return None
    local = evento.datos
    solicitud = revision.eventos_revision.filter(tipo='ia_solicitud').last()
    if solicitud is None:
        return local
    resultado = revision.eventos_revision.filter(
        tipo='ia_resultado', operacion=uuid5(solicitud.operacion, 'resultado-ia'),
    ).first()
    if resultado is None:
        return {**local, 'ia': {'estado': 'en_proceso', 'detalle': 'Solicitud de IA en proceso.'}}
    datos = resultado.datos
    return {
        **local, 'ia': datos['ia'], 'hallazgos': [*local['hallazgos'], *datos['hallazgos']],
        'juridico': {**local['juridico'], 'interpretacion_propuesta': datos['interpretacion_propuesta']},
    }


def contexto_contractual_minimo(contrato):
    return {
        'numero_contrato': contrato.numero_contrato, 'vigencia': contrato.anio_contractual,
        'fecha_inicio': contrato.fecha_inicio.isoformat() if contrato.fecha_inicio else None,
        'fecha_terminacion': contrato.fecha_terminacion.isoformat() if contrato.fecha_terminacion else None,
        'valor_actual': str(contrato.valor_actual),
    }


def diagnostico_tecnico_financiero(datos, obligaciones):
    auxiliar = datos['auxiliar_pagos']
    balance = [
        {clave: renglon.get(clave) for clave in ('clave', 'valor', 'documento_id', 'version')}
        for renglon in datos['datos'] if renglon['clase'] == 'balance' and renglon['estado'] == 'verificado'
    ]
    return {
        'resumen': 'Cotejo de los soportes registrados. El auxiliar de pagos no certifica el saldo final.',
        'pagos_acreditados': auxiliar['valor'],
        'operaciones_acreditadas': len(auxiliar['operaciones_economicas']),
        'fuentes_pago': auxiliar['ids'],
        'balance_verificado': balance,
        'garantias_registradas': len(datos['garantias']),
        'obligaciones_registradas': len(obligaciones),
        'obligaciones_sin_soporte': [o['numero'] for o in obligaciones if not o['documentos']],
        'advertencias': datos['advertencias'],
    }


@transaction.atomic
def ejecutar_revision(*, usuario, revision, operacion):
    from .revision_analisis import analizar

    bloquear_calendarios()
    contrato = _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
    firma = huella(['analizar', revision.pk])
    repetido = _preexistente(revision, usuario, 'analisis', operacion, firma)
    if repetido:
        return repetido
    if analisis_registrado(revision) is not None:
        raise ValidationError('Esta entrega ya tiene análisis. Registre una nueva entrega para conservar el anterior.')
    datos_financieros = panorama(contrato)
    calculo = calculo_verificado_vigente(contrato)
    fecha = fecha_confirmada(contrato) if calculo else None
    obligaciones = [ficha(o) for o in vigentes(contrato)]
    requisitos = resumen_documental(contrato)
    contexto = {
        **contexto_contractual_minimo(contrato),
        'contratista_nombre': contrato.contratista_nombre, 'contratista_identificacion': contrato.contratista_identificacion,
        'valor_inicial': str(contrato.valor_inicial), 'requisitos': requisitos['requisitos'],
        'pagos': datos_financieros.get('pagos', []), 'garantias': datos_financieros.get('garantias', []),
        'pagos_acreditados': datos_financieros.get('pagos_acreditados', []),
        'obligaciones': obligaciones,
        'competencia': {'fecha_confirmada': fecha.isoformat() if fecha else None, 'calculo_id': calculo.pk if calculo else None,
                       'vigente': bool(calculo and fecha)},
        'referencias_juridicas': getattr(settings, 'SIGELC_REFERENCIAS_REVISION', []),
    }
    resultado = analizar(documentos_analizables(revision), contexto)
    resultado['juridico'].update(
        diagnostico_tecnico_financiero=diagnostico_tecnico_financiero(datos_financieros, obligaciones),
        lista_documental={'requisitos': requisitos['requisitos'], 'advertencia': requisitos['aviso']},
        terminos_verificados={clave: calculo.resultado.get(clave) for clave in (
            'fin_bilateral', 'fin_unilateral', 'limite_competencia',
        )} if calculo else None,
        ambito_revision='Diagnóstico al momento de la revisión. Compruebe la vigencia de los términos antes de usarlos.',
    )
    return _evento(revision, usuario, 'analisis', operacion, firma, resultado)


def decisiones_vigentes(revision):
    decisiones = {}
    for evento in revision.eventos_revision.filter(tipo='decision'):
        decisiones[evento.datos['hallazgo_id']] = evento
    return decisiones


@transaction.atomic
def decidir(*, usuario, revision, operacion, accion, hallazgo_id='', descripcion='', fundamento='',
           decision_esperada=None, documento_id=None, ubicacion='', fragmento=''):
    _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
    firma = huella([accion, hallazgo_id, descripcion, fundamento, decision_esperada, documento_id, ubicacion, fragmento])
    repetido = _preexistente(revision, usuario, 'decision', operacion, firma)
    if repetido:
        return repetido
    analisis = analisis_registrado(revision)
    if not analisis:
        raise ValidationError('Ejecute las verificaciones antes de registrar observaciones.')
    if accion == 'manual':
        hallazgo_id = f'manual-{operacion}'
        evidencia = []
        if documento_id:
            fuente = revision.fuentes_revision.filter(pk=documento_id).first()
            if not fuente or not fragmento or not any(
                f['ubicacion'] == ubicacion and fragmento in f['texto'] for f in fuente.fragmentos
            ):
                raise ValidationError('El fragmento debe existir en la ubicación y el documento indicados.')
            evidencia = [{'documento_id': documento_id, 'ubicacion': ubicacion, 'fragmento': fragmento}]
        elif not fundamento.strip():
            raise ValidationError('Indique el fundamento de la observación manual o vincule un fragmento.')
        hallazgo = {'categoria': 'otro', 'evidencia': evidencia, 'regla': '', 'descripcion': ''}
    else:
        hallazgo = next((h for h in analisis['hallazgos'] if str(h['id']) == hallazgo_id), None)
        if not hallazgo:
            raise ValidationError('Seleccione un hallazgo de esta revisión.')
    previo = decisiones_vigentes(revision).get(hallazgo_id)
    if (previo.pk if previo else None) != decision_esperada:
        raise ValidationError('La decisión cambió. Recargue la revisión.')
    if accion in ('manual', 'editar') and not descripcion.strip():
        raise ValidationError('Indique el texto de la observación.')
    if accion in ('editar', 'rechazar') and not fundamento.strip():
        raise ValidationError('Indique el fundamento del cambio de criterio.')
    return _evento(revision, usuario, 'decision', operacion, firma, {
        'accion': accion, 'hallazgo_id': hallazgo_id, 'anterior_id': previo.pk if previo else None,
        'descripcion': descripcion.strip() if accion in ('manual', 'editar') else hallazgo['descripcion'],
        'fundamento': fundamento.strip(), 'evidencia': hallazgo.get('evidencia', []),
        'categoria': hallazgo.get('categoria', 'otro'), 'regla': hallazgo.get('regla', ''),
    })


def _observacion_para_documento(decision, revision, usuario, guardar):
    datos = decision.datos
    operacion = uuid5(decision.operacion, 'observacion-aprobada')
    anterior = ObservacionDocumental.objects.filter(operacion=operacion).first()
    if anterior:
        return anterior
    referencias = []
    soportes = []
    for evidencia in datos['evidencia']:
        fuente = revision.fuentes_revision.get(pk=evidencia['documento_id'])
        referencias.append(f"{fuente.nombre} · {evidencia['ubicacion']}: {evidencia['fragmento']}")
        if fuente.tipo not in ('docx', 'xlsx', 'pdf'):
            continue
        clave = uuid5(revision.operacion, f'fuente-confirmada-{fuente.pk}')
        soporte = DocumentoContrato.objects.filter(operacion_recepcion=clave, contrato=revision.contrato).first()
        if not soporte:
            with fuente.archivo.open('rb') as archivo:
                contenido = archivo.read()
            if sha256(contenido).hexdigest() != fuente.sha256:
                raise ValidationError('El archivo original no coincide con la huella registrada.')
            soporte = DocumentoContrato(
                contrato=revision.contrato, tipo_documento='Soporte de revisión', nombre_original=fuente.nombre,
                cargado_por=usuario, fecha_recepcion=timezone.localdate(revision.fecha),
                operacion_recepcion=clave, descripcion=f'Fuente de revisión {revision.pk} · SHA-256 {fuente.sha256}',
            )
            guardar(soporte.archivo, soporte.nombre_original, contenido)
            soporte.save(force_insert=True)
            _auditar(usuario, 'revision_soporte', revision, {'fuente_id': fuente.pk, 'documento_id': soporte.pk})
        soportes.append(soporte)
    descripcion = datos['descripcion']
    if datos['fundamento']:
        descripcion += f"\nFundamento registrado por el abogado: {datos['fundamento']}"
    tipo = 'faltante' if datos['regla'] == 'requisitos' and not datos['evidencia'] else 'inconsistencia'
    return registrar_observacion(
        usuario=usuario, contrato_id=revision.contrato_id, descripcion=descripcion, tipo=tipo,
        estado_revision=EstadoRevision.OBSERVADO, evidencia='\n'.join(referencias) or str(datos['regla'] or datos['fundamento']),
        documentos=soportes, operacion=operacion,
    )


def generar_desde_revision(*, usuario, revision, operacion, tipo, observaciones, confirmado):
    if confirmado is not True or not observaciones or len(observaciones) != len(set(observaciones)):
        raise ValidationError('Confirme qué observaciones aprobadas se incluirán.')
    firma = huella([tipo, sorted(observaciones)])
    with escritura_con_archivo() as guardar:
        bloquear_calendarios()
        contrato = _contrato(usuario, revision.contrato_id, 'change_documentocontrato')
        exigir(usuario, 'add_documentocontrato')
        repetido = _preexistente(revision, usuario, 'generacion', operacion, firma)
        if repetido:
            return repetido
        instancia = _estado_actual(contrato)
        etapa = contrato.estado_actual.codigo
        if (etapa != revision.etapa or etapa not in ETAPAS or not instancia
                or instancia.pk != revision.instancia_id or instancia.estado_id != contrato.estado_actual_id
                or instancia.plazo_dias_habiles != ETAPAS[etapa] or not contrato.estado_actual.activo):
            raise ValidationError('La entrega debe corresponder a una instancia vigente de requerimiento.')
        if contrato.revisiones_inteligentes.first().pk != revision.pk:
            raise ValidationError('Genere el nuevo borrador desde la última entrega. Las versiones previas se conservan.')
        vigentes = {e.pk: e for e in decisiones_vigentes(revision).values() if e.datos['accion'] != 'rechazar'}
        if not set(observaciones) <= set(vigentes):
            raise ValidationError('La selección contiene propuestas pendientes, rechazadas o rectificadas.')
        decisiones = [vigentes[pk] for pk in observaciones]
        elegidas = [_observacion_para_documento(d, revision, usuario, guardar) for d in decisiones]
        metadata = {
            'id': revision.pk, 'version': revision.version, 'fecha': timezone.localdate(revision.fecha).isoformat(),
            'etapa': etapa, 'plazo': instancia.plazo_dias_habiles, 'vencimiento': revisar_instancia(instancia),
            'observaciones_ids': [o.pk for o in elegidas], 'decisiones_ids': observaciones,
            'textos': [o.descripcion for o in elegidas],
        }
        documento = generar_requerimiento(
            usuario=usuario, contrato_id=contrato.pk, tipo=TipoRequerimiento.OBSERVACIONES if tipo == 'acta' else etapa,
            operacion=uuid5(operacion, 'borrador-revision'), exigir_etapa_vigente=tipo != 'acta',
            revision_confirmada=metadata,
        )
        return _evento(revision, usuario, 'generacion', operacion, firma, {'borrador_id': documento.pk, 'tipo': tipo})
