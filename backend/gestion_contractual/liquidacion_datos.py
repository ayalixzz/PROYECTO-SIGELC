"""Insumos versionados: evidencia y revisión, sin certificar acuerdos ni saldos."""
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Auditoria, Contrato, GarantiaContractual, PagoContractual, DatoLiquidacion
from .requerimientos import exigir

MODELOS={'garantia':GarantiaContractual,'pago':PagoContractual,'dato':DatoLiquidacion}
BALANCE={'valor_inicial','adiciones','total','ejecutado','pagado','saldo','reintegro','liberado'}
REPRESENTACION={'responsable_entidad_nombre','responsable_entidad_id','cargo_entidad','facultades_entidad',
                'representante_legal','representante_id','facultades_contratista'}
CONTRACTUAL={'plazo','dependencia','ingreso_almacen','revisor_uno','revisor_dos','aprobador'}
DECLARACIONES={'cumplimiento','seguridad_social','sanciones','ecuacion','reclamaciones','paz_salvo'}
CLAVES={'representacion':REPRESENTACION,'balance':BALANCE,'contractual':CONTRACTUAL,'declaracion':DECLARACIONES}
CAMPOS={
 'garantia':['clave','seccion','aseguradora','poliza','anexo','fecha_expedicion','fecha_aprobacion','amparo','valor_asegurado','vigencia_inicio','vigencia_fin','documento','estado','fundamento'],
 'pago':['clave','naturaleza','referencia','rubro','orden','fecha','valor','certificador','fecha_certificacion','documento','estado','fundamento','operacion_economica'],
 'dato':['clave','clase','texto','valor','emisor','fecha_fuente','documento','estado','fundamento'],
}


def vigentes(modelo,contrato):
    return modelo.objects.filter(contrato=contrato,rectificacion__isnull=True).select_related('documento','responsable','validado_por').order_by('clave')


def ficha(obj):
    if obj.documento_id and (obj.documento.contrato_id!=obj.contrato_id or hasattr(obj.documento,'generacion')):
        raise ValidationError('Fuente documental de otro contrato o documento generado no admisible como certificación.')
    def valor(v):
        return v.isoformat() if isinstance(v,(datetime,date)) else str(v) if isinstance(v,(Decimal,UUID)) else v
    datos={f.name:valor(getattr(obj,f.attname)) for f in obj._meta.concrete_fields}
    datos['documento_fuente']=({'id':obj.documento_id,'nombre':obj.documento.nombre_original,'version':obj.documento.version} if obj.documento_id else None)
    return datos


@transaction.atomic
def registrar_insumo(*,usuario,contrato_id,tipo,operacion,anterior=None,motivo='',**datos):
    exigir(usuario,'view_contrato','view_documentocontrato','change_documentocontrato')
    if tipo not in MODELOS or set(datos)-set(CAMPOS[tipo]):raise ValidationError('Tipo o campos de insumo no admitidos.')
    modelo=MODELOS[tipo]
    contrato=Contrato.objects.select_for_update().get(pk=contrato_id)
    try:op=UUID(str(operacion))
    except (ValueError,TypeError,AttributeError) as exc:raise ValidationError('Operación no válida.') from exc
    if isinstance(datos.get('clave'),str):datos['clave']=datos['clave'].strip()
    previo=modelo.objects.filter(operacion=op).first()
    if previo:
        if previo.contrato_id!=contrato.pk or previo.responsable_id!=usuario.pk:raise ValidationError('Operación en uso.')
        if previo.anterior_id != (anterior.pk if anterior else None):raise ValidationError('Reintento con versión anterior distinta.')
        for campo, valor in datos.items():
            if campo=='operacion_economica' and valor is None:continue
            guardado=previo._meta.get_field(campo).value_from_object(previo)
            valor=getattr(valor,'pk',valor)
            if isinstance(guardado,Decimal) and valor is not None:valor=Decimal(str(valor))
            if guardado!=valor:raise ValidationError('La operación ya existe con otros datos; utilice una nueva operación o rectificación.')
        return previo
    clave=datos.get('clave','').strip()
    if not clave:raise ValidationError('Indique una clave estable para el registro.')
    if anterior:
        anterior=vigentes(modelo,contrato).filter(pk=anterior.pk).first()
        if anterior is None or anterior.clave!=clave or not motivo.strip():raise ValidationError('Rectifique la versión vigente de la misma clave e indique el motivo.')
    elif modelo.objects.filter(contrato=contrato,clave=clave).exists():raise ValidationError('La clave ya existe; registre una rectificación.')
    documento=datos.get('documento')
    if documento and (documento.contrato_id!=contrato.pk or hasattr(documento,'generacion')):raise ValidationError('El soporte debe ser un documento fuente del mismo contrato.')
    verificado=datos.get('estado','pendiente')=='verificado'
    if verificado and (not documento or not datos.get('fundamento','').strip()):raise ValidationError('La verificación exige documento y referencia precisa.')
    if tipo=='garantia':
        if datos.get('vigencia_inicio') and datos.get('vigencia_fin') and datos['vigencia_fin']<datos['vigencia_inicio']:raise ValidationError('Vigencia final anterior a la inicial.')
        necesarios=['aseguradora','poliza','anexo','fecha_expedicion','fecha_aprobacion','amparo','valor_asegurado','vigencia_inicio','vigencia_fin']
    elif tipo=='pago':
        necesarios=['orden','fecha','valor','certificador','fecha_certificacion']
    else:
        clase=datos.get('clase')
        if clase not in CLAVES or clave not in CLAVES[clase]:raise ValidationError('Clave y clase de dato no compatibles.')
        if anterior and anterior.clase!=clase:raise ValidationError('Conserve la clase del dato al rectificar.')
        if clase!='balance' and datos.get('valor') is not None:raise ValidationError('El importe corresponde exclusivamente a campos del balance.')
        necesarios=['valor'] if clase=='balance' else ['texto','emisor','fecha_fuente']
    if verificado and any(datos.get(k) is None or datos.get(k)=='' for k in necesarios):raise ValidationError('Complete los campos y fuente antes de verificar; cero y ausencia son diferentes.')
    if tipo in ('garantia','pago'):
        identidad=['seccion','poliza','anexo','amparo'] if tipo=='garantia' else ['referencia','orden','rubro','fecha','valor','naturaleza']
        duplicados=vigentes(modelo,contrato).filter(**{k:datos.get(k) for k in identidad})
        if anterior:duplicados=duplicados.exclude(pk=anterior.pk)
        if tipo=='pago' and datos.get('operacion_economica'):
            duplicados=duplicados.filter(documento=datos.get('documento'))
        if all(datos.get(k) is not None for k in identidad) and duplicados.exists():raise ValidationError('Ya existe un registro vigente con esa referencia; rectifique su versión.')
    if tipo=='pago':
        from .pagos_integridad import preparar_pago
        preparar_pago(contrato,usuario,datos,anterior)
    obj=modelo(contrato=contrato,**{**datos,'clave':clave},responsable=usuario,operacion=op,anterior=anterior,
        version=anterior.version+1 if anterior else 1,motivo=motivo.strip(),
        validado_por=usuario if verificado else None,validado_en=timezone.now() if verificado else None)
    obj.save(force_insert=True)
    Auditoria.objects.create(usuario=usuario,accion='rectificar_insumo_liquidacion' if anterior else 'registrar_insumo_liquidacion',
        entidad_afectada=modelo.__name__,identificador_registro=str(obj.pk),datos_nuevos=ficha(obj),
        datos_anteriores={'anterior_id':anterior.pk} if anterior else {})
    return obj


def dinero(valor):
    if valor is None:return '[PENDIENTE]'
    return '$ '+f'{Decimal(valor):,.2f}'.replace(',','X').replace('.',',').replace('X','.')


def panorama(contrato):
    garantias=[ficha(o) for o in vigentes(GarantiaContractual,contrato)]
    pagos=[ficha(o) for o in vigentes(PagoContractual,contrato)]
    datos=[ficha(o) for o in vigentes(DatoLiquidacion,contrato)]
    from .pagos_integridad import operaciones_acreditadas
    acreditados, ambiguos=operaciones_acreditadas(pagos)
    total=sum((Decimal(p['valor']) for p in acreditados),Decimal('0.00')) if acreditados else None
    balance={d['clave']:d for d in datos if d['clase']=='balance' and d['estado']=='verificado'}
    avisos=[]
    if ambiguos:avisos.append('Pagos históricos sin identidad o con evidencias inconsistentes pendientes de revisión: '+', '.join(map(str,ambiguos))+'. No se fusionan ni acreditan automáticamente.')
    if any(p['posible_duplicado'] for p in pagos):avisos.append('Hay registros marcados como posibles duplicados; revisar sin fusionar históricos.')
    if total is None:avisos.append('No hay pagos efectivamente realizados acreditados; la suma no se presume cero.')
    if any(p['estado']!='verificado' or p['naturaleza'] not in ('realizado','tesoreria') for p in pagos):avisos.append('Existen facturas, pagos programados o datos sin verificación que no se tratan como pagos realizados.')
    if 'pagado' in balance and total is not None and Decimal(balance['pagado']['valor'])!=total:avisos.append('Diferencia entre el valor pagado del balance revisado y la suma auxiliar de pagos acreditados.')
    if {'valor_inicial','adiciones','total'}<=balance.keys() and Decimal(balance['valor_inicial']['valor'])+Decimal(balance['adiciones']['valor'])!=Decimal(balance['total']['valor']):avisos.append('Diferencia aritmética entre valor inicial, adiciones y total revisados.')
    return {'garantias':garantias,'pagos':pagos,'datos':datos,'pagos_acreditados':acreditados,
            'auxiliar_pagos':{'formula':'Suma Decimal de una sola cuantía por operación económica acreditada, con evidencias vigentes verificadas de realización o Tesorería; múltiples soportes no duplican el desembolso y no certifica saldo.',
                              'ids':[p['id'] for p in acreditados],'versiones':[p['version'] for p in acreditados],
                              'operaciones_economicas':[p['operacion_economica'] for p in acreditados],
                              'evidencias':[p['evidencias'] for p in acreditados],
                              'valor':str(total) if total is not None else None},'advertencias':avisos}
