"""Política de avisos internos sobre fechas confirmadas por los servicios vigentes."""

from collections.abc import Mapping
from datetime import date, timedelta

from .calendario import calendario_disponible, dependencia


HITOS_OPERATIVOS = ((1, 'alta'), (3, 'alta'), (5, 'media'))
HITOS_COMPETENCIA = ((1, 'critica'), (7, 'alta'), (15, 'alta'), (30, 'media'))
PRIORIDAD = {'critica': 0, 'alta': 1, 'media': 2}
_DIA = timedelta(days=1)
# Evita recorrer históricos ni depender de series interminables sin días hábiles.
_MAX_DIAS_EXPLORADOS = 366


def _fecha(valor):
    if type(valor) is date:
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor)
        except ValueError:
            return None
    return None


def _habiles_restantes(hoy, vencimiento, proveedor):
    """Cuenta (hoy, vencimiento] y solo con calendarios aprobados.

    Devuelve None si la cobertura es insuficiente o la fecha registrada no
    corresponde a un día hábil. En cualquiera de los dos casos se omite el
    aviso próximo, sin asignar una fecha sustitutiva.
    """
    limite = HITOS_OPERATIVOS[-1][0]
    dias_habiles = 0
    calendarios = {}
    explorados = 0
    fecha = hoy
    try:
        while fecha < vencimiento and explorados < _MAX_DIAS_EXPLORADOS:
            fecha += _DIA
            explorados += 1
            if fecha.year not in calendarios:
                calendarios[fecha.year] = proveedor(fecha.year)
            dias, fuente = calendarios[fecha.year]
            revision = dependencia(fecha, dias, fuente)
            if not revision['aprobado']:
                return None
            if revision['habil']:
                dias_habiles += 1
            if dias_habiles > limite:
                return dias_habiles
        if fecha == vencimiento and revision['habil']:
            return dias_habiles
    except (ValueError, OverflowError):
        return None
    return None


def _alerta(tipo, objetivo, dias):
    juridica = tipo == 'competencia'
    tema = 'Fecha de competencia registrada' if juridica else 'Vencimiento operativo'
    unidad = 'calendario' if juridica else 'habiles'
    if dias is None or dias < 0:
        hito, estado = 'vencida', 'vencida'
        prioridad = 'critica' if juridica else 'alta'
        titulo = f'{tema} superado' if not juridica else f'{tema} superada'
    elif dias == 0:
        hito, estado = 'hoy', 'hoy'
        prioridad = 'critica' if juridica else 'alta'
        titulo = f'{tema} para hoy'
    else:
        hitos = HITOS_COMPETENCIA if juridica else HITOS_OPERATIVOS
        franja = next(((limite, nivel) for limite, nivel in hitos if dias <= limite), None)
        if franja is None:
            return None
        limite, prioridad = franja
        hito, estado = str(limite), 'proxima'
        plazo = ('día' if dias == 1 else 'días') + (
            ' calendario' if juridica else ' hábil' if dias == 1 else ' hábiles'
        )
        titulo = f'{tema} en {dias} {plazo}'
    fecha = objetivo.isoformat()
    return {
        'tipo': tipo,
        'fecha_objetivo': fecha,
        'hito': hito,
        'estado': estado,
        'dias_restantes': dias,
        'unidad': unidad,
        'prioridad': prioridad,
        'titulo': titulo,
        'clave': f'{tipo}:{fecha}:{hito}',
    }


def evaluar_alertas(*, hoy, vencimiento_operativo=None, fecha_juridica=None,
                    proveedor_calendario=calendario_disponible):
    """Entrega como máximo una alerta vigente por cada tipo de fecha confiable.

    La fecha jurídica debe venir de `competencia.fecha_confirmada`. Una ficha
    de vencimiento operativo se acepta únicamente si el servicio la presenta
    como confirmada. Los umbrales son ventanas inclusivas: se conserva el hito
    de la ventana vigente y no se fabrican avisos históricos omitidos.
    """
    if type(hoy) is not date:
        raise ValueError('Indique la fecha local de corte.')
    alertas = []
    competencia = _fecha(fecha_juridica)
    if competencia:
        alerta = _alerta('competencia', competencia, (competencia - hoy).days)
        if alerta:
            alertas.append(alerta)

    if isinstance(vencimiento_operativo, Mapping) and vencimiento_operativo.get('estado') == 'confirmado':
        vencimiento = _fecha(vencimiento_operativo.get('fecha'))
        if vencimiento and vencimiento > hoy:
            restantes = _habiles_restantes(hoy, vencimiento, proveedor_calendario)
            if restantes is not None:
                alerta = _alerta('operativa', vencimiento, restantes)
                if alerta:
                    alertas.append(alerta)
        elif vencimiento:
            restantes = 0 if vencimiento == hoy else None
            alertas.append(_alerta('operativa', vencimiento, restantes))

    return sorted(alertas, key=lambda aviso: (PRIORIDAD[aviso['prioridad']], aviso['tipo'] != 'competencia'))
