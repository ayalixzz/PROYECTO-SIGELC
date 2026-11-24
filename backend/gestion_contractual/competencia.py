from calendar import monthrange
from datetime import date, timedelta
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .calendario import calendario_disponible, dependencia, es_dia_habil
from .models import Auditoria, CalculoCompetencia, Contrato, DocumentoContrato
from .requerimientos import exigir


Estado = CalculoCompetencia.Estado
FECHAS_RESULTADO = ('fecha_base', 'fin_bilateral', 'fin_unilateral', 'inicio_judicial', 'fin_judicial', 'limite_competencia')
FUENTES = {
    'Ley 1150 de 2007, artículo 11': 'https://normograma.icfes.gov.co/Compilacion/docs/ley_1150_2007.htm',
    'CPACA, artículo 164.2.j': 'https://www.cancilleria.gov.co/normograma/compilacion/docs/ley_1437_2011.htm',
    'Ley 4 de 1913, artículos 59 y 62': 'https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=8426',
    'Consejo de Estado, auto de unificación 62009 de 2019': 'https://www.consejodeestado.gov.co/autosu/tercera/13_05001-23-33-000-2018-00342-01%2862009%29.pdf',
    'Corte Constitucional, SU-016 de 2026': 'https://www.corteconstitucional.gov.co/relatoria/2026/SU016-26',
}


def sumar_meses(fecha, meses):
    anio, mes = divmod(fecha.year * 12 + fecha.month - 1 + meses, 12)
    mes += 1
    return date(anio, mes, min(fecha.day, monthrange(anio, mes)[1]))


def sumar_anios(fecha, anios):
    return sumar_meses(fecha, anios * 12)


def evaluar(insumos, *, calendario=calendario_disponible):
    resultado = {
        'fecha_base': None, 'fin_bilateral': None, 'fin_unilateral': None,
        'inicio_judicial': None, 'fin_judicial': None, 'limite_competencia': None,
        'reglas': [], 'advertencias': [], 'faltantes': [], 'fuentes': FUENTES,
        'version_reglas': 'C-1', 'calendarios': {},
        'plazo_bilateral': None, 'plazo_unilateral': None,
        'dependencias_calendario': {'esquema':'G-1','regla_juridica':'C-1','independiente':False,'fechas':[],
            'alcance':'Control de inhabilidad de fechas nominales; no determina vacancia judicial ni traslada meses/años.'},
        'advertencias_calendario': [],
    }
    reglas = resultado['reglas']
    alertas = resultado['advertencias']
    faltantes = resultado['faltantes']

    def fecha(clave):
        valor = insumos.get(clave)
        return date.fromisoformat(valor) if valor else None

    def registrar(clave, valor):
        resultado[clave] = valor.isoformat()

    def revisar_vencimiento(valor, etiqueta):
        try:
            festivos, fuente = calendario(valor.year)
            resultado['calendarios'][str(valor.year)] = fuente
            d = dependencia(valor, festivos, fuente)
            d['uso'] = etiqueta
            resultado['dependencias_calendario']['fechas'].append(d)
            if not d['aprobado']:
                resultado['advertencias_calendario'].append(f'{valor.year}: calendario no aprobado; propuesta provisional, no verificable.')
            if not es_dia_habil(valor, festivos_verificados=festivos):
                alertas.append(f'{etiqueta} cae en día no hábil. Revise el traslado aplicable y las fechas dependientes.')
        except ValueError:
            alertas.append(f'Falta calendario verificable para {etiqueta}.')

    origen = insumos.get('origen')
    base = fecha({'ejecucion': 'fecha_terminacion', 'efectiva': 'fecha_efectiva'}.get(origen, 'fecha_referencia')) if origen else None
    if base:
        registrar('fecha_base', base)
    if insumos.get('sujeto') == 'no':
        resultado['dependencias_calendario']['independiente'] = True
        reglas.append('El abogado indicó que el expediente no está sujeto a liquidación. Esta proyección no aplica.')
        return Estado.PENDIENTE, resultado
    if insumos.get('sujeto') != 'si':
        faltantes.append('Determinación de sujeción a liquidación')
    if not insumos.get('regimen'):
        faltantes.append('Régimen aplicable')
    if not base:
        faltantes.append('Origen y fecha base documentados')
    if origen and origen != 'ejecucion' and not (insumos.get('soporte_terminacion') or insumos.get('referencias', '').strip()):
        faltantes.append('Soporte o referencia del hecho, acto o acuerdo de terminación')
    if faltantes:
        return Estado.INFORMACION, resultado
    if insumos['regimen'] != 'art11_cpaca' or origen == 'otra':
        alertas.append('El régimen o la fecha de referencia requiere determinación jurídica específica.')
        return Estado.REVISION, resultado

    pacto = insumos.get('pacto')
    if pacto not in ('si', 'no'):
        faltantes.append('Confirmación sobre existencia de plazo pactado')
        return Estado.INFORMACION, resultado
    if pacto == 'si':
        cantidad, unidad, referencia = insumos.get('cantidad'), insumos.get('unidad'), fecha('referencia_pacto')
        if not cantidad or not unidad or not referencia:
            faltantes.append('Duración, unidad y fecha de referencia del plazo pactado')
            return Estado.INFORMACION, resultado
        if unidad not in ('meses', 'anios', 'dias_calendario'):
            alertas.append('El pacto exige interpretación de la unidad y del calendario aplicable.')
            return Estado.REVISION, resultado
        if type(cantidad) is not int or cantidad < 1 or referencia < base:
            alertas.append('Revise la duración o la fecha de referencia pactada frente a la fecha base.')
            return Estado.REVISION, resultado
        bilateral = {'meses': sumar_meses, 'anios': sumar_anios,
                     'dias_calendario': lambda inicio, n: inicio + timedelta(days=n)}[unidad](referencia, cantidad)
        resultado['plazo_bilateral'] = {'cantidad': cantidad, 'unidad': unidad, 'origen': 'pacto'}
        reglas.append(f'Plazo bilateral pactado: {cantidad} {unidad} desde {referencia}.')
    else:
        bilateral = sumar_meses(base, 4)
        resultado['plazo_bilateral'] = {'cantidad': 4, 'unidad': 'meses', 'origen': 'supletivo'}
        reglas.append('Artículo 11, plazo bilateral supletivo: cuatro meses calendario.')
    registrar('fin_bilateral', bilateral)
    revisar_vencimiento(bilateral, 'El fin bilateral nominal')
    unilateral = sumar_meses(bilateral, 2)
    resultado['plazo_unilateral'] = {'cantidad': 2, 'unidad': 'meses'}
    registrar('fin_unilateral', unilateral)
    revisar_vencimiento(unilateral, 'El fin unilateral nominal')
    reglas.append('Artículo 11, ventana unilateral nominal de dos meses posteriores, condicionada a la convocatoria y sus resultados.')

    previa = insumos.get('liquidacion')
    if previa not in ('ninguna', 'bilateral', 'unilateral'):
        faltantes.append('Situación de liquidación previa')
    if previa == 'ninguna' and any(fecha(k) for k in ('fecha_liquidacion', 'firma_acta', 'ejecutoria_acto')):
        alertas.append('Se indicó que no hubo liquidación, pero se registraron fechas de una actuación liquidatoria.')
    if insumos.get('incidencias') != 'no':
        if insumos.get('incidencias') == 'si':
            alertas.append('Hay circunstancias que pueden alterar el cómputo. Revise el expediente y las referencias.')
        else:
            faltantes.append('Confirmación sobre demanda, conciliación, suspensiones u otras incidencias')
    if previa in ('bilateral', 'unilateral'):
        hito = fecha('firma_acta' if previa == 'bilateral' else 'ejecutoria_acto')
        if hito is None or fecha('fecha_liquidacion') is None:
            faltantes.append('Fecha del acta o acto y su firma bilateral o ejecutoria, según corresponda')
        else:
            fecha_actuacion = fecha('fecha_liquidacion')
            fuera_de_plazo = (hito > bilateral if previa == 'bilateral' else
                              not bilateral < fecha_actuacion <= unilateral)
            if fecha_actuacion < base or hito < fecha_actuacion or fuera_de_plazo:
                alertas.append('La actuación presenta fechas inconsistentes o extemporáneas. Revise el precedente aplicable, incluida SU-016/26 cuando corresponda.')
            elif not faltantes and not alertas:
                inicio = hito + timedelta(days=1)
                registrar('inicio_judicial', inicio)
                fin = sumar_anios(inicio, 2)
                registrar('fin_judicial', fin)
                revisar_vencimiento(fin, 'El fin judicial nominal')
                reglas.append('CPACA, literal j, ordinal iii o iv: desde el día siguiente a la firma bilateral o ejecutoria, respectivamente.')
    elif previa == 'ninguna' and not faltantes and not alertas:
        inicio = unilateral + timedelta(days=1)
        fin = sumar_anios(inicio, 2)
        registrar('inicio_judicial', inicio)
        registrar('fin_judicial', fin)
        revisar_vencimiento(fin, 'El fin judicial nominal')
        limite = sumar_anios(unilateral, 2)
        revisar_vencimiento(limite, 'El límite de competencia nominal')
        if not alertas:
            registrar('limite_competencia', limite)
        reglas.extend(['CPACA, literal j, ordinal v: evaluar dos años desde el día siguiente al agotamiento de la ventana unilateral.',
                       'Artículo 11, ventana adicional nominal de dos años desde el vencimiento unilateral. El límite se conserva separado del término judicial.'])
    estado = Estado.INFORMACION if faltantes else Estado.REVISION if alertas else Estado.PENDIENTE
    return estado, resultado


def datos_contrato(contrato):
    return {'fecha_terminacion': contrato.fecha_terminacion.isoformat() if contrato.fecha_terminacion else None,
            'tipo_contrato': contrato.tipo_contrato, 'modalidad_contratacion': contrato.modalidad_contratacion}


@transaction.atomic
def calculo_verificado_vigente(contrato):
    from .calendarios_servicios import bloquear_calendarios, evaluar_vigencia
    bloquear_calendarios()
    contrato = Contrato.objects.get(pk=contrato.pk)
    actual = contrato.calculos_competencia.first()
    if (actual and actual.estado == Estado.VERIFICADO
            and all(actual.insumos.get(k) == v for k, v in datos_contrato(contrato).items())
            and evaluar_vigencia(actual)['clasificacion'] == 'sin_impacto'):
        return actual


def fecha_confirmada(contrato):
    actual = calculo_verificado_vigente(contrato)
    if actual:
        valor = actual.resultado.get('limite_competencia')
        return date.fromisoformat(valor) if valor else None


@transaction.atomic
def registrar_calculo(*, contrato_id, usuario, operacion, anterior_id, datos=None, accion='calcular',
                      fundamento='', motivo='', rectificacion=None):
    exigir(usuario, 'view_contrato', 'change_contrato')
    from .calendarios_servicios import bloquear_calendarios, evaluar_vigencia
    bloquear_calendarios()
    contrato = Contrato.objects.select_for_update().get(pk=contrato_id)
    if contrato.abogado_responsable_id != usuario.pk:
        raise PermissionDenied
    repetida = CalculoCompetencia.objects.filter(operacion=operacion).first()
    if repetida:
        if repetida.contrato_id != contrato.pk or repetida.creado_por_id != usuario.pk:
            raise ValidationError('Identificador de operación en uso.')
        return repetida
    previo = contrato.calculos_competencia.first()
    if anterior_id != (previo.pk if previo else None):
        raise ValidationError('El cálculo vigente cambió. Recargue la ficha.')
    actuales = datos_contrato(contrato)
    if datos is not None and any(k in datos and datos[k] != v for k, v in actuales.items()):
        raise ValidationError('Los datos contractuales cambiaron. Recargue la ficha.')
    insumos = {**(datos if datos is not None else previo.insumos if previo else {}), **actuales}
    soporte = insumos.get('soporte_terminacion')
    if soporte and not DocumentoContrato.objects.filter(pk=soporte, contrato=contrato).exists():
        raise ValidationError('El soporte de terminación debe pertenecer al contrato.')
    cambios = [k for k in set(insumos) | set(previo.insumos if previo else {}) if not previo or previo.insumos.get(k) != insumos.get(k)]
    if accion == 'verificar':
        if not previo or cambios or previo.estado != Estado.PENDIENTE or not fundamento.strip():
            raise ValidationError('Verifique una propuesta vigente y completa e indique su fundamento jurídico.')
        if evaluar_vigencia(previo)['clasificacion'] != 'sin_impacto':
            raise ValidationError('Las dependencias de calendario cambiaron, no están aprobadas o no son demostrables. Genere y revise una propuesta actualizada.')
        estado, resultado = Estado.VERIFICADO, previo.resultado
    elif accion == 'rectificar':
        if not previo or cambios or not motivo.strip() or not fundamento.strip() or not rectificacion:
            raise ValidationError('La rectificación requiere una versión vigente, motivo y fundamento.')
        try:
            if set(rectificacion) != set(FECHAS_RESULTADO):
                raise ValueError
            fechas = {k: date.fromisoformat(v) if v else None for k, v in rectificacion.items()}
        except (ValueError, TypeError) as error:
            raise ValidationError('Indique fechas válidas para la rectificación.') from error
        if not fechas['fecha_base'] or not any(fechas[k] for k in FECHAS_RESULTADO[1:]):
            raise ValidationError('La rectificación debe identificar la fecha base y al menos un vencimiento o hito.')
        for inicio, fin in (('fecha_base', 'fin_bilateral'), ('fin_bilateral', 'fin_unilateral'), ('inicio_judicial', 'fin_judicial'), ('fecha_base', 'limite_competencia')):
            if fechas[inicio] and fechas[fin] and fechas[inicio] > fechas[fin]:
                raise ValidationError('Revise el orden cronológico de las fechas rectificadas.')
        estado = Estado.PENDIENTE
        resultado = {**previo.resultado, **rectificacion}
        resultado['dependencias_calendario'] = {'esquema':'G-1','regla_juridica':resultado['version_reglas'],
            'independiente':False, 'fechas':[], 'alcance':'Rectificación manual fundamentada; no certifica calendario judicial.'}
        for clave in ('fin_bilateral','fin_unilateral','fin_judicial','limite_competencia'):
            if fechas[clave]:
                dias, fuente = calendario_disponible(fechas[clave].year)
                d = dependencia(fechas[clave],dias,fuente)
                d['uso'] = clave + ': rectificación manual'
                resultado['dependencias_calendario']['fechas'].append(d)
        resultado['reglas'] = [*resultado['reglas'], f'Rectificación manual pendiente de verificación: {fundamento.strip()}']
    elif accion == 'calcular':
        try:
            estado, resultado = evaluar(insumos)
        except (OverflowError, ValueError) as error:
            raise ValidationError('Revise las fechas y duración ingresadas.') from error
        if datos is None and previo and any(k in actuales for k in cambios):
            estado = Estado.REVISION if estado == Estado.PENDIENTE else estado
            resultado['advertencias'].append('Cambió la información contractual. Revise la fecha base y los demás insumos jurídicos antes de verificar.')
    else:
        raise ValidationError('Operación inválida.')
    verificado = estado == Estado.VERIFICADO
    registro = CalculoCompetencia.objects.create(
        contrato=contrato, anterior=previo, version=previo.version + 1 if previo else 1,
        operacion=operacion, creado_por=usuario, estado=estado, insumos=insumos,
        resultado=resultado, cambios=sorted(cambios), motivo_rectificacion=motivo.strip(),
        fundamento_verificacion=fundamento.strip() if verificado else '',
        verificado_por=usuario if verificado else None, verificado_en=timezone.now() if verificado else None,
        calculado_en=previo.calculado_en if verificado else timezone.now(),
    )
    Auditoria.objects.create(usuario=usuario, accion=f'{accion}_competencia', entidad_afectada='CalculoCompetencia',
                             identificador_registro=str(registro.pk), datos_nuevos={'contrato_id': contrato.pk,
                             'version': registro.version, 'estado': estado, 'cambios': registro.cambios})
    return registro


def actualizar_por_edicion(contrato, usuario):
    previo = contrato.calculos_competencia.first()
    if previo and any(previo.insumos.get(k) != v for k, v in datos_contrato(contrato).items()):
        return registrar_calculo(
            contrato_id=contrato.pk, usuario=usuario, operacion=uuid4(), anterior_id=previo.pk,
            motivo='Actualización de datos contractuales desde Admin.',
        )
