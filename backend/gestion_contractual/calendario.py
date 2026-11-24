from datetime import date, timedelta
from hashlib import sha256
import json


POLITICA_OPERATIVA = {'tipo': 'administrativo_operativo', 'dias_laborables': [0, 1, 2, 3, 4],
                     'regla': 'G-1', 'alcance': 'Seguimiento operativo; no es calendario judicial ni autoriza traslados jurídicos.'}


class DiasCalendario(set):
    def __init__(self, fechas, *, version=None, politica=None, excepciones=()):
        super().__init__(fechas)
        self.version = version
        self.politica = politica or dict(POLITICA_OPERATIVA)
        self.excepciones = {e['fecha']: e['habil'] for e in excepciones}


def huella(valor):
    return sha256(json.dumps(valor, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dependencia(fecha, dias, fuente):
    version = getattr(dias, 'version', None)
    politica = getattr(dias, 'politica', POLITICA_OPERATIVA)
    return {'fecha': fecha.isoformat(), 'anio': fecha.year, 'version_id': getattr(version, 'pk', None),
            'version': getattr(version, 'version', None), 'aprobado': bool(version and version.estado == 'aprobado'),
            'habil': es_dia_habil(fecha, festivos_verificados=dias), 'politica': huella(politica),
            'regla': 'G-1', 'fuente': fuente}


def es_dia_habil(fecha, *, festivos_verificados):
    """Comprueba una fecha frente a una lista suministrada de festivos aplicables.

    No calcula festivos ni plazos. El llamador debe proporcionar un calendario
    colombiano verificado para el año y la entidad antes de usar este resultado.
    """
    if not isinstance(fecha, date) or festivos_verificados is None:
        raise ValueError('Se requiere una fecha y un calendario verificado de festivos.')
    excepciones = getattr(festivos_verificados, 'excepciones', {})
    if fecha.isoformat() in excepciones:
        return excepciones[fecha.isoformat()]
    politica = getattr(festivos_verificados, 'politica', POLITICA_OPERATIVA)
    return fecha.weekday() in politica['dias_laborables'] and fecha not in festivos_verificados


FUENTE_NACIONAL = 'Ley 51 de 1983, art. 1 y Ley 2578 de 2026, art. 6 (revisión 2026-11-14)'


def _pascua(anio):
    """Computus gregoriano de Meeus, Jones y Butcher."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def calendario_nacional(anio):
    if anio < 1984:
        raise ValueError('El calendario normativo disponible comienza en 1984.')
    fijos = [(1, 1), (5, 1), (7, 20), (8, 7), (12, 8), (12, 25)]
    trasladables = [(1, 6), (3, 19), (6, 29), (8, 15), (10, 12), (11, 1), (11, 11)]
    if anio >= 2026:
        trasladables.append((7, 9))
    pascua = _pascua(anio)
    festivos = {date(anio, mes, dia) for mes, dia in fijos}
    festivos.update(pascua - timedelta(days=dias) for dias in (3, 2))
    a_lunes = [date(anio, mes, dia) for mes, dia in trasladables]
    a_lunes.extend(pascua + timedelta(days=dias) for dias in (39, 60, 68))
    festivos.update(fecha + timedelta(days=(-fecha.weekday()) % 7) for fecha in a_lunes)
    return festivos, FUENTE_NACIONAL


def preparar_nacional(anio):
    """Pure proposal, no database writes or approval; sources have explicit temporal scope."""
    festivos, _ = calendario_nacional(anio)
    pascua = _pascua(anio)
    items = []
    fijos = [(1,1),(5,1),(7,20),(8,7),(12,8),(12,25)]
    trasladables = [(1,6),(3,19),(6,29),(8,15),(10,12),(11,1),(11,11)]
    for mes, dia in fijos:
        f = date(anio,mes,dia)
        items.append({'conmemoracion':f.isoformat(),'descanso':f.isoformat(),'tipo':'fijo','norma':'Ley 51 de 1983, art. 1'})
    for f in [date(anio,m,d) for m,d in trasladables] + [pascua+timedelta(days=d) for d in (39,60,68)]:
        items.append({'conmemoracion':f.isoformat(),'descanso':(f+timedelta(days=(-f.weekday())%7)).isoformat(),'tipo':'trasladable','norma':'Ley 51 de 1983, art. 1.2'})
    for d in (3,2):
        f=pascua-timedelta(days=d)
        items.append({'conmemoracion':f.isoformat(),'descanso':f.isoformat(),'tipo':'semana_santa','norma':'Ley 51 de 1983, art. 1'})
    if anio >= 2026:
        f=date(anio,7,9)
        items.append({'conmemoracion':f.isoformat(),'descanso':(f+timedelta(days=(-f.weekday())%7)).isoformat(),
                      'tipo':'trasladable','norma':'Ley 2578 de 2026, arts. 6 y 7','vigencia_desde':'2026-06-02'})
    for item in items:
        item['fuente'] = ('https://lector.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%202026/Ley%202578%20de%202026.pdf'
                          if item['norma'].startswith('Ley 2578') else
                          'https://jurinfo.jep.gov.co/Normograma/Compilacion/docs/ley_0051_1983.htm')
    return {'anio':anio,'festivos':sorted(f.isoformat() for f in festivos),'fundamentos':sorted(items,key=lambda x:(x['descanso'],x['conmemoracion'])),
            'politica':dict(POLITICA_OPERATIVA),'excepciones':[],
            'advertencias':['Propuesta nacional: requiere revisión de fuentes y aprobación manual.',
                            'Política operativa lunes a viernes previamente utilizada; confirmar actos institucionales y excepciones.',
                            'No incorpora vacancia judicial ni normas especiales futuras.'],
            'fuentes':['https://jurinfo.jep.gov.co/Normograma/Compilacion/docs/ley_0051_1983.htm',
                       'https://lector.ramajudicial.gov.co/SIDN/NORMATIVA/TEXTOS_COMPLETOS/7_LEYES/LEYES%202026/Ley%202578%20de%202026.pdf']}


def calcular_vencimiento(inicio, dias, *, proveedor=calendario_nacional):
    if dias is None:
        return None, {}
    if dias <= 0:
        raise ValueError('El plazo debe ser positivo.')
    fecha = inicio
    calendarios = {}
    procedencia = {}
    while dias:
        fecha += timedelta(days=1)
        if fecha.year not in calendarios:
            calendarios[fecha.year], fuente = proveedor(fecha.year)
            procedencia[str(fecha.year)] = {'esquema': 'G-1', 'fuente': fuente, 'fechas': []}
        registro = procedencia[str(fecha.year)]
        registro['fechas'].append(dependencia(fecha, calendarios[fecha.year], registro['fuente']))
        registro['aprobado'] = all(d['aprobado'] for d in registro['fechas'])
        if es_dia_habil(fecha, festivos_verificados=calendarios[fecha.year]):
            dias -= 1
    return fecha, procedencia


def actualizar_calendario(anio, obtener_verificado):
    """Persiste una versión anual completa entregada por un proveedor confiable."""
    from django.core.validators import URLValidator
    from .models import CalendarioVerificado

    festivos, fuente = obtener_verificado(anio)
    URLValidator(schemes=['https'])(fuente)
    fechas = set(festivos)
    if not fechas or any(type(fecha) is not date or fecha.year != anio for fecha in fechas):
        raise ValueError('Se requiere un conjunto anual de fechas verificadas.')
    base, _ = calendario_nacional(anio)
    if not base.issubset(fechas):
        raise ValueError('La versión anual omite festivos de la base normativa vigente.')
    return CalendarioVerificado.objects.create(anio=anio, fuente=fuente, festivos=sorted(fecha.isoformat() for fecha in fechas))


def calendario_disponible(anio):
    from .models import CalendarioVerificado

    version = CalendarioVerificado.objects.filter(anio=anio, estado='aprobado').order_by('-version', '-pk').first()
    if version is None:
        fechas, fuente = calendario_nacional(anio)
        return DiasCalendario(fechas), f'PROVISIONAL: sin calendario aprobado {anio}. {fuente}'
    return DiasCalendario((date.fromisoformat(fecha) for fecha in version.festivos), version=version,
                          politica=version.politica, excepciones=version.excepciones), f'{version.fuente} (versión {version.pk})'
