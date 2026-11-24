"""Versioned approvals and evidence-based impact. No automatic legal date shifting."""
from datetime import date
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import connection, transaction
from django.utils import timezone

from .calendario import (POLITICA_OPERATIVA, DiasCalendario, calendario_disponible,
                         calendario_nacional, calcular_vencimiento, dependencia,
                         es_dia_habil, preparar_nacional)
from .models import Auditoria, CalendarioVerificado, CalculoCompetencia, Contrato, EvaluacionCalendario, InstanciaSeguimiento
from .requerimientos import exigir


def bloquear_calendarios(*, exclusivo=False):
    """Always before contract locks, held to outer commit; readers share a registry gate."""
    if not connection.in_atomic_block:
        raise RuntimeError('El bloqueo del calendario requiere una transacción activa.')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(731921, 21)' if exclusivo else
                       'SELECT pg_advisory_xact_lock_shared(731921, 21)')


def vigente(anio):
    return CalendarioVerificado.objects.filter(anio=anio, estado='aprobado').order_by('-version').first()


def estado_vigencia(version):
    if version.estado == 'aprobado' and vigente(version.anio).pk != version.pk:
        return 'REEMPLAZADO'
    return {'legacy':'REQUIERE_REVISION','propuesta':'PENDIENTE_VALIDACION','aprobado':'APROBADO'}[version.estado]


def _dias(version):
    return DiasCalendario([date.fromisoformat(f) for f in version.festivos], version=version,
                          politica=version.politica, excepciones=version.excepciones)


def comparar(anterior, nueva):
    """Read-only and deterministic, including classifications and non-date policy changes."""
    if anterior and anterior.anio != nueva.anio:
        raise ValidationError('Compare versiones del mismo año.')
    base = _dias(anterior) if anterior else DiasCalendario(calendario_nacional(nueva.anio)[0])
    actuales = _dias(nueva)
    fechas = sorted(set(base) | set(actuales) | {date.fromisoformat(f) for f in base.excepciones} |
                    {date.fromisoformat(f) for f in actuales.excepciones})
    return {'anio':nueva.anio, 'anterior_id':getattr(anterior,'pk',None),
            'base_aprobada':bool(anterior and anterior.estado=='aprobado'),
            'politica_base_documentada':bool(anterior and anterior.politica),
            'anadidos':sorted(f.isoformat() for f in actuales-base),
            'retirados':sorted(f.isoformat() for f in base-actuales),
            'clasificacion':[{'fecha':f.isoformat(),'antes':es_dia_habil(f,festivos_verificados=base),
                              'despues':es_dia_habil(f,festivos_verificados=actuales)} for f in fechas
                             if es_dia_habil(f,festivos_verificados=base) != es_dia_habil(f,festivos_verificados=actuales)],
            'politica_modificada':base.politica != actuales.politica,
            'motivo':nueva.justificacion, 'fuente':nueva.fuente, 'desde':f'{nueva.anio}-01-01','hasta':f'{nueva.anio}-12-31'}


def _auditar(usuario, accion, objeto, datos):
    Auditoria.objects.create(usuario=usuario, accion=accion, entidad_afectada=type(objeto).__name__,
                             identificador_registro=str(objeto.pk), datos_nuevos=datos)


def _reintento(operacion, usuario, estado):
    previo = CalendarioVerificado.objects.filter(operacion=operacion).first()
    if previo and (previo.responsable_id != usuario.pk or previo.estado != estado):
        raise ValidationError('Identificador de operación en uso.')
    return previo


@transaction.atomic
def proponer(*, anio, usuario, fuente, motivo, operacion, anterior_id=None, excepciones=(), politica=None):
    exigir(usuario, 'view_contrato', 'change_contrato')
    bloquear_calendarios(exclusivo=True)
    previo = _reintento(operacion, usuario, 'propuesta')
    if previo:
        if (previo.anio != anio or previo.justificacion != motivo.strip() or previo.fuente != fuente
                or not isinstance(excepciones,(list,tuple)) or not all(isinstance(e,dict) for e in excepciones)
                or previo.excepciones != sorted(list(excepciones),key=lambda e:str(e.get('fecha','')))
                or previo.politica != (politica or POLITICA_OPERATIVA)):
            raise ValidationError('El reintento no corresponde a la misma propuesta.')
        return previo
    URLValidator(schemes=['https'])(fuente)
    if not motivo.strip():
        raise ValidationError('Indique la justificación de la propuesta.')
    actual = vigente(anio)
    if anterior_id != getattr(actual, 'pk', None):
        raise ValidationError('La versión aprobada cambió. Recargue el calendario.')
    datos = preparar_nacional(anio)
    politica = dict(politica or POLITICA_OPERATIVA)
    # Changes in ordinary workweeks need a separate institutional policy decision.
    if politica != POLITICA_OPERATIVA:
        raise ValidationError('No hay autorización institucional para cambiar la política ordinaria; registre excepciones justificadas por fecha.')
    if not isinstance(excepciones,(list,tuple)):
        raise ValidationError('Las excepciones deben ser una lista de objetos.')
    excepciones = list(excepciones)
    vistas = set()
    nacionales = set(datos['festivos'])
    for e in excepciones:
        if (not isinstance(e, dict) or set(e) != {'fecha','habil','motivo','fuente'}
                or not all(isinstance(e[k],str) for k in ('fecha','motivo','fuente'))):
            raise ValidationError('Cada excepción requiere fecha, habil, motivo y fuente.')
        try:
            f = date.fromisoformat(e['fecha'])
            URLValidator(schemes=['https'])(e['fuente'])
        except (TypeError, ValueError) as error:
            raise ValidationError('Excepción inválida.') from error
        if f.year != anio or type(e['habil']) is not bool or not e['motivo'].strip() or f in vistas:
            raise ValidationError('Revise el año, clasificación y fundamento de las excepciones.')
        if e['habil'] and e['fecha'] in nacionales:
            raise ValidationError('Una excepción operativa no puede derogar un festivo nacional.')
        vistas.add(f)
    ultima = CalendarioVerificado.objects.filter(anio=anio, version__isnull=False).order_by('-version').first()
    version = CalendarioVerificado(anio=anio, version=ultima.version+1 if ultima else 1, estado='propuesta',
        anterior=actual, responsable=usuario, fuente=fuente, justificacion=motivo.strip(), operacion=operacion,
        politica=politica, excepciones=sorted(excepciones,key=lambda e:e['fecha']),
        fundamentos=datos['fundamentos'], advertencias=datos['advertencias'], festivos=datos['festivos'])
    version.diferencias = comparar(actual, version)
    version._por_servicio = True
    version.save(force_insert=True)
    _auditar(usuario,'proponer_calendario',version,version.diferencias)
    # Potential impacts never replace authoritative evaluations or approvals.
    _auditar(usuario,'impacto_potencial_calendario',version,{'sustituye_aprobado':False,'diferencias':version.diferencias})
    return version


def evaluar_dependencias(resultado):
    deps = resultado.get('dependencias_calendario')
    if not isinstance(deps,dict) or deps.get('esquema') != 'G-1' or deps.get('regla_juridica') != 'C-1' or resultado.get('version_reglas') != 'C-1':
        return {'clasificacion':'incierto','motivo':'Dependencias históricas o regla no demostrable; nueva revisión requerida.'}
    if (deps.get('independiente') is True and not deps.get('fechas')
            and not any(resultado.get(k) for k in ('fin_bilateral','fin_unilateral','inicio_judicial','fin_judicial','limite_competencia'))):
        return {'clasificacion':'sin_impacto','motivo':'D: proyección expresamente no aplicable; no publica una fecha de competencia.'}
    if not deps.get('fechas'):
        return {'clasificacion':'incierto','motivo':'No hay evidencia de las fechas consultadas.'}
    comprobadas = []
    afectado = False
    incierto = False
    for d in deps['fechas']:
        try:
            f = date.fromisoformat(d['fecha'])
            dias, fuente = calendario_disponible(f.year)
            ahora = dependencia(f,dias,fuente)
            comprobadas.append({'antes':d,'ahora':ahora})
            if not d.get('aprobado') or not ahora['aprobado'] or d.get('regla') != 'G-1' or d.get('politica') != ahora['politica']:
                incierto = True
            elif d['habil'] != ahora['habil']:
                afectado = True
        except (KeyError,TypeError,ValueError):
            incierto = True
    clasificacion = 'afectado' if afectado else 'incierto' if incierto else 'sin_impacto'
    return {'clasificacion':clasificacion,'motivo':{'afectado':'B: cambió una premisa de habilitación consultada; requiere nueva propuesta y revisión.',
            'incierto':'C: cobertura o dependencia no demostrable; requiere nueva revisión.',
            'sin_impacto':'A: mismas clasificaciones y reglas en todas las fechas consultadas; no se trasladan meses/años.'}[clasificacion],
            'comprobaciones':comprobadas}


def evaluar_vigencia(calculo):
    revision = evaluar_dependencias(calculo.resultado)
    previa = calculo.evaluaciones_calendario.filter(accion='evaluar',clasificacion__in=['afectado','incierto']).first()
    if previa and revision['clasificacion'] == 'sin_impacto':
        revision = {**revision,'clasificacion':'incierto',
                    'motivo':'La versión perdió vigencia en una evaluación anterior. Aunque se restituyó el calendario, requiere nueva propuesta y validación jurídica.',
                    'evaluacion_anterior_id':previa.pk}
    return revision


def revisar_instancia(instancia):
    if not instancia.plazo_dias_habiles:
        return {'clasificacion':'sin_impacto','motivo':'Sin plazo configurado.', 'historico':None,'vigente':None,'teorico':None,'procedencia':{}}
    teorico, procedencia = calcular_vencimiento(instancia.fecha_inicio,instancia.plazo_dias_habiles,proveedor=calendario_disponible)
    confirmacion = instancia.evaluaciones_calendario.filter(accion='confirmar').first()
    conservado = date.fromisoformat(confirmacion.detalle['teorico']) if confirmacion else instancia.fecha_vencimiento
    origen = confirmacion.detalle['procedencia'] if confirmacion else instancia.procedencia_calendario
    completa = all(isinstance(v,dict) and v.get('esquema')=='G-1' and v.get('aprobado') for v in origen.values()) and bool(origen)
    completa = completa and all(v['aprobado'] for v in procedencia.values())
    # Full recomputation is evidence of an unchanged operational deadline, only with known provenance.
    clasificacion = 'afectado' if teorico != conservado else 'sin_impacto' if completa else 'incierto'
    return {'clasificacion':clasificacion,'motivo':'Vencimiento teórico cotejado sin sobrescribir el original.',
            'historico':instancia.fecha_vencimiento.isoformat(),'vigente':conservado.isoformat(),'teorico':teorico.isoformat(),
            'cerrada':bool(instancia.fecha_finalizacion),'procedencia':procedencia,
            'provisional':not all(v['aprobado'] for v in procedencia.values())}


def _guardar_evaluacion(*, calendario, usuario, instancia=None, calculo=None, detalle):
    e=EvaluacionCalendario(calendario=calendario,usuario=usuario,instancia=instancia,calculo=calculo,
                          accion='evaluar',clasificacion=detalle['clasificacion'],detalle=detalle,
                          motivo=detalle['motivo'],operacion=uuid4())
    e._por_servicio=True
    e.save(force_insert=True)
    _auditar(usuario,'evaluar_impacto_calendario',e,{'clasificacion':e.clasificacion,'calendario_id':calendario.pk,
                                                  'instancia_id':e.instancia_id,'calculo_id':e.calculo_id})
    if calculo and calculo.estado == CalculoCompetencia.Estado.VERIFICADO and e.clasificacion != 'sin_impacto':
        _auditar(usuario,'revisar_vigencia_juridica',e,{'calculo_id':calculo.pk,'publicacion_confirmada_bloqueada':True})
    return e


@transaction.atomic
def aprobar(*, propuesta_id, usuario, operacion, confirmado, fundamento):
    exigir(usuario,'view_contrato','change_contrato')
    bloquear_calendarios(exclusivo=True)
    repetida=_reintento(operacion,usuario,'aprobado')
    if repetida:
        if repetida.propuesta_id != propuesta_id or repetida.justificacion != fundamento.strip():
            raise ValidationError('Reintento distinto de la aprobación original.')
        return repetida
    p=CalendarioVerificado.objects.get(pk=propuesta_id)
    actual=vigente(p.anio)
    if p.estado != 'propuesta' or p.anterior_id != getattr(actual,'pk',None) or hasattr(p,'aprobacion'):
        raise ValidationError('Propuesta obsoleta o ya aprobada. Genere y revise una nueva versión.')
    if confirmado is not True or not fundamento.strip():
        raise ValidationError('Confirme la revisión normativa y la política institucional e indique fundamento.')
    ultima=CalendarioVerificado.objects.filter(anio=p.anio,version__isnull=False).order_by('-version').first()
    n=CalendarioVerificado(anio=p.anio,version=ultima.version+1,estado='aprobado',anterior=actual,propuesta=p,
        responsable=usuario,fuente=p.fuente,justificacion=fundamento.strip(),operacion=operacion,aprobado_en=timezone.now(),
        festivos=p.festivos,politica=p.politica,excepciones=p.excepciones,fundamentos=p.fundamentos,advertencias=p.advertencias)
    n.diferencias=comparar(actual,n)
    n._por_servicio=True
    n.save(force_insert=True)
    _auditar(usuario,'aprobar_calendario',n,{'anterior_id':n.anterior_id,'diferencias':n.diferencias})
    if actual:
        _auditar(usuario,'reemplazar_calendario',n,{'anterior_id':actual.pk,'historia_conservada':True})
    # Include all dependent historical rows, not merely the current contract calculation.
    for i in InstanciaSeguimiento.objects.filter(fecha_inicio__year__lte=n.anio,fecha_vencimiento__year__gte=n.anio):
        _guardar_evaluacion(calendario=n,usuario=usuario,instancia=i,detalle=revisar_instancia(i))
    for c in CalculoCompetencia.objects.all().iterator():
        deps=c.resultado.get('dependencias_calendario',{})
        anios={str(d.get('anio')) for d in deps.get('fechas',[])} | set(c.resultado.get('calendarios',{}))
        # Legacy rows without provenance are uncertain, but unrelated years are not invalidated.
        if str(n.anio) in anios or (not deps and not anios):
            _guardar_evaluacion(calendario=n,usuario=usuario,calculo=c,detalle=evaluar_vigencia(c))
    return n


@transaction.atomic
def confirmar_vencimiento(*, evaluacion_id, usuario, motivo, confirmado, operacion):
    exigir(usuario,'view_contrato','change_contrato')
    bloquear_calendarios()
    e=EvaluacionCalendario.objects.select_related('instancia','instancia__contrato').get(pk=evaluacion_id)
    if not e.instancia_id or e.accion != 'evaluar' or e.instancia.contrato.abogado_responsable_id != usuario.pk:
        raise ValidationError('La evaluación no corresponde a una instancia del abogado.')
    Contrato.objects.select_for_update().get(pk=e.instancia.contrato_id)
    repetida=EvaluacionCalendario.objects.filter(operacion=operacion).first()
    if repetida:
        if repetida.anterior_id != e.pk or repetida.usuario_id != usuario.pk or repetida.motivo != motivo.strip():
            raise ValidationError('Identificador de operación en uso.')
        return repetida
    e.instancia.refresh_from_db()
    if e.instancia.fecha_finalizacion or confirmado is not True or not motivo.strip():
        raise ValidationError('Solo se confirma una etapa abierta con justificación expresa; no corrige notificaciones emitidas.')
    ultima=e.instancia.evaluaciones_calendario.filter(accion='evaluar').first()
    detalle=revisar_instancia(e.instancia)
    if ultima.pk != e.pk or detalle != e.detalle or detalle.get('provisional') or hasattr(e,'confirmacion'):
        raise ValidationError('La evaluación cambió o no tiene cobertura aprobada; recargue y revise.')
    r=EvaluacionCalendario(calendario=e.calendario,instancia=e.instancia,accion='confirmar',clasificacion='sin_impacto',
        detalle=detalle,anterior=e,usuario=usuario,motivo=motivo.strip(),operacion=operacion)
    r._por_servicio=True
    r.save(force_insert=True)
    _auditar(usuario,'confirmar_vencimiento',r,{'original':detalle['historico'],'confirmado':detalle['teorico'],'instancia_id':e.instancia_id})
    return r


def vencimiento_documental(instancia, teorico):
    """Return effective date and conspicuous draft qualification; old snapshots stay immutable."""
    revision=revisar_instancia(instancia)
    efectivo=revision['vigente']
    if teorico.isoformat() != efectivo or teorico.isoformat() != revision['teorico']:
        raise ValidationError('El vencimiento difiere del calendario vigente; requiere revisión y confirmación expresa.')
    provisional=revision['clasificacion']=='incierto'
    return efectivo + (' [PROVISIONAL: calendario pendiente de validación]' if provisional else ''), revision
