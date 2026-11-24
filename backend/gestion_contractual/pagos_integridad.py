"""Una identidad económica, múltiples evidencias; nunca se fusionan históricos."""
from decimal import Decimal
from django.core.exceptions import ValidationError

ACREDITABLES = ('realizado', 'tesoreria')
IDENTIDAD = ('referencia', 'orden', 'rubro', 'fecha')
CONSISTENCIA = ('orden', 'rubro', 'fecha', 'valor')


def preparar_pago(contrato, usuario, datos, anterior):
    from .models import OperacionPago, PagoContractual
    actuales = PagoContractual.objects.filter(contrato=contrato, rectificacion__isnull=True)
    if anterior:
        actuales = actuales.exclude(pk=anterior.pk)
    op = datos.get('operacion_economica') or (anterior.operacion_economica if anterior else None)
    if op and op.contrato_id != contrato.pk:
        raise ValidationError('La operación económica debe pertenecer al mismo contrato.')
    if anterior and anterior.operacion_economica_id and op.pk != anterior.operacion_economica_id and not anterior.posible_duplicado:
        raise ValidationError('La rectificación conserva la identidad económica; no reasigne el pago.')
    posible = actuales.filter(naturaleza__in=ACREDITABLES, **{k:datos.get(k) for k in IDENTIDAD})
    if op:
        posible = posible.exclude(operacion_economica=op)
    ambiguos = posible.exists() and datos.get('naturaleza') in ACREDITABLES
    if ambiguos and datos.get('estado') == 'verificado':
        raise ValidationError('Posible duplicado: registre pendiente para revisión o seleccione expresamente la operación económica ya acreditada. No se fusionan pagos automáticamente.')
    if op and datos.get('estado') == 'verificado' and datos.get('naturaleza') in ACREDITABLES:
        otras = actuales.filter(operacion_economica=op, estado='verificado', naturaleza__in=ACREDITABLES)
        for otra in otras:
            if any(getattr(otra,k) != datos.get(k) for k in CONSISTENCIA):
                raise ValidationError('Las evidencias de un mismo desembolso difieren: rectifique y revise sus versiones antes de acreditar.')
    if op is None:
        op = OperacionPago.objects.create(contrato=contrato, referencia=datos['referencia'], responsable=usuario)
    datos['operacion_economica'] = op
    datos['posible_duplicado'] = ambiguos


def operaciones_acreditadas(pagos):
    grupos = {}
    pendientes = []
    for pago in pagos:
        if pago['estado'] != 'verificado' or pago['naturaleza'] not in ACREDITABLES:
            continue
        identidad = pago['operacion_economica']
        if not identidad or pago['posible_duplicado']:
            pendientes.append(pago['id'])
            continue
        grupos.setdefault(identidad, []).append(pago)
    resultado = []
    for identidad, fuentes in grupos.items():
        if len({tuple(str(f[k]) for k in CONSISTENCIA) for f in fuentes}) != 1:
            pendientes.extend(f['id'] for f in fuentes)
            continue
        pago = dict(fuentes[0])
        pago['evidencias'] = [{'id':f['id'], 'version':f['version'], 'documento':f['documento'],
                              'fuente':f['documento_fuente'], 'naturaleza':f['naturaleza']} for f in fuentes]
        resultado.append(pago)
    return resultado, pendientes
