from hashlib import sha256
from io import BytesIO
from xml.etree import ElementTree as E
from zipfile import ZipFile
from django.core.exceptions import ValidationError
from .docx_requerimientos import procesar_docx
from .liquidacion_datos import panorama,dinero
from .liquidacion_docx import adaptar

ROTULO='BORRADOR — PENDIENTE DE REVISIÓN JURÍDICA Y FINANCIERA'


def es_institucional(contenido):
    return 'rotulo_liquidacion' in procesar_docx(contenido)[1]


def contexto(contrato,plantilla,contenido,usuario=None):
    from .actas import contexto_acta
    valores,fuentes,soportes,calculo=contexto_acta(contrato)
    marcadores=procesar_docx(contenido)[1]
    valores={k:'[PENDIENTE]' for k in marcadores}|{k:v for k,v in valores.items() if v is not None and v!=''}
    datos=panorama(contrato)
    revisados={d['clave']:d for d in datos['datos'] if d['estado']=='verificado' and d['clase']!='balance'}
    balance={d['clave']:d for d in datos['datos'] if d['estado']=='verificado' and d['clase']=='balance'}
    for clave in ('responsable_entidad_nombre','responsable_entidad_id','cargo_entidad','facultades_entidad','representante_legal','representante_id','facultades_contratista','plazo','dependencia','ingreso_almacen','revisor_uno','revisor_dos','aprobador'):
        valores[clave]=revisados[clave]['texto'] if clave in revisados else '[PENDIENTE]'
        fuentes[clave]=f"DatoLiquidacion #{revisados[clave]['id']} v{revisados[clave]['version']}" if clave in revisados else 'Sin fuente verificada; diligenciamiento pendiente'
    valores['representacion_entidad']=f"{valores['responsable_entidad_nombre']}, identificación {valores['responsable_entidad_id']}, cargo {valores['cargo_entidad']}; facultades: {valores['facultades_entidad']}"
    valores['representacion_contratista']=f"{valores['representante_legal']}, identificación {valores['representante_id']}; facultades: {valores['facultades_contratista']}"
    valores['valor_inicial_registrado']=dinero(contrato.valor_inicial)+' (valor registrado, pendiente de verificación financiera)'
    valores['rotulo_liquidacion']=ROTULO;valores['suscripcion_acta']='[PENDIENTE: sin suscripción ni formalización]'
    for field,key in [('direccion','direccion_entidad'),('telefono','telefono_entidad'),('correo_institucional','correo_entidad')]:valores[key]=getattr(contrato.entidad,field) or '[PENDIENTE]'
    for clave in ('valor_inicial','adiciones','total','ejecutado','pagado','saldo','reintegro','liberado'):
        valores['balance_'+clave]=dinero(balance[clave]['valor']) if clave in balance else '[PENDIENTE: verificación financiera]'
        fuentes['balance_'+clave]=f"DatoLiquidacion #{balance[clave]['id']} v{balance[clave]['version']}" if clave in balance else 'Importe desconocido; no equivale a cero'
    for clave in ('cumplimiento','seguridad_social','sanciones','ecuacion','reclamaciones','paz_salvo'):
        d=revisados.get(clave)
        valores['declaracion_'+clave]=(f"Texto propuesto revisado, pendiente de adopción y suscripción por los responsables: {d['texto']} (fuente #{d['documento']}, emisor {d['emisor']}, fecha {d['fecha_fuente']})." if d else '[PENDIENTE: declaración sustentada y revisada]')
    valores['suma_pagos']=dinero(datos['auxiliar_pagos']['valor'])
    valores['referencia_pagos']='Solo pagos realizados acreditados; las facturas y pagos programados no se incluyen como desembolsos. '+ '; '.join(f"#{p['id']} · fuente #{p['documento']} · {p['certificador']} · certificación {p['fecha_certificacion']}" for p in datos['pagos_acreditados'])
    gcof=list(contrato.borradores_requerimiento.filter(tipo='gcof19').values('id','version','estado_revision').order_by('version'))
    obligaciones=list(contrato.obligaciones.filter(rectificacion__isnull=True).values('id','tipo','numero','version','estado_verificacion','porcentaje'))
    for ob in obligaciones:ob['porcentaje']=str(ob['porcentaje']) if ob['porcentaje'] is not None else None
    observaciones=list(contrato.observaciones_documentales.filter(rectificacion__isnull=True).values('id','descripcion','tipo','estado_revision','evidencia'))
    valores['referencias_cumplimiento']=f"Expediente: {len(gcof)} borradores GCOF19 (no certifican cumplimiento); {len(obligaciones)} obligaciones vigentes; {len(observaciones)} observaciones vigentes. La declaración exige fuente formal y revisión específica."
    referencias={}
    for o in datos['garantias']+datos['pagos']+datos['datos']:
        if o['documento_fuente']:
            d=o['documento_fuente'];referencias[d['id']]=f"Fuente #{d['id']} {d['nombre']} v{d['version']}"
    valores['referencias_fuentes']='\n'.join(referencias.values()) or '[PENDIENTE: fuentes documentales]'
    datos['advertencias']+=['Garantías: la suficiencia y aplicabilidad requieren revisión específica; las fechas no la acreditan.','El borrador no formaliza acuerdos, renuncias ni paz y salvo y no cambia estados.']
    if not calculo:datos['advertencias'].append('Sin cálculo jurídico vigente verificado; no se concluye competencia.')
    valores['fecha_limite_competencia']=calculo.resultado.get('limite_competencia') if calculo else '[PENDIENTE: cálculo jurídico vigente verificado]'
    valores['advertencias_liquidacion']='\n'.join(datos['advertencias'])
    valores['elaboro']=(usuario or contrato.abogado_responsable).get_full_name() or '[PENDIENTE: nombre del elaborador]'
    for g in datos['garantias']:g['valor_formato']=dinero(g['valor_asegurado'])
    for p in datos['pagos_acreditados']:p['valor_formato']=dinero(p['valor'])
    cuerpo,filas=adaptar(contenido,datos['garantias'],datos['pagos_acreditados']);valores.update(filas)
    usados=procesar_docx(cuerpo)[1]
    pendientes={k for k in usados if valores.get(k) is None or '[PENDIENTE' in str(valores.get(k,''))}
    pendientes|={f"{grupo}_{o['id']}_verificacion" for grupo in ('garantias','pagos','datos') for o in datos[grupo] if o['estado']!='verificado'}
    metadata={'liquidacion_institucional':True,'insumos_liquidacion':datos,'referencias_gcof19':gcof,'obligaciones_origen':obligaciones,'observaciones_origen':observaciones,
              'plantilla_origen':{'id':plantilla.pk,'version':plantilla.version,'sha256':sha256(contenido).hexdigest()},
              'calculo_origen':{'id':calculo.pk,'version':calculo.version,'insumos':calculo.insumos,'resultado':calculo.resultado} if calculo else None,'pendientes_documentales':sorted(pendientes)}
    procedencia={k:fuentes.get(k,'Insumos de liquidación versionados; sin fuente verificada se indica pendiente') for k in usados}
    return cuerpo,valores,procedencia,metadata,calculo


def validar_revision(contenido,motivo):
    if not motivo.strip():raise ValidationError('Indique motivo de revisión del acta institucional.')
    with ZipFile(BytesIO(contenido)) as z:root=E.fromstring(z.read('word/document.xml'))
    if ROTULO not in ''.join(root.itertext()):raise ValidationError('Conserve el rótulo de revisión jurídica y financiera del borrador.')
