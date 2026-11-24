from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as E
from django.core.exceptions import ValidationError
from .docx_requerimientos import procesar_docx

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def adaptar(contenido,garantias,pagos):
    if not {'rotulo_liquidacion','g_principal_amparo','g_extension_amparo','p_valor'}<=procesar_docx(contenido)[1]:
        raise ValidationError('No corresponde a la plantilla institucional de liquidación parametrizada.')
    with ZipFile(BytesIO(contenido)) as z:root=E.fromstring(z.read('word/document.xml'))
    tabla=next((t for t in root.iter(W+'tbl') if '{{g_principal_amparo}}' in ''.join(t.itertext())),None)
    if tabla is None or len(tabla.findall(W+'tr'))!=37:raise ValidationError('Estructura institucional de liquidación incompatible.')
    rows=tabla.findall(W+'tr');valores={}
    def cambiar(row,mapping):
        for t in row.iter(W+'t'):
            for a,b in mapping.items():t.text=(t.text or '').replace('{{'+a+'}}','{{'+b+'}}')
    nuevos=[]
    for seccion,inicio in [('principal',9),('extension',15)]:
        grupos={}
        for g in garantias:
            if g['seccion']==seccion:
                key=tuple(g[k] for k in ('aseguradora','poliza','anexo','fecha_expedicion','fecha_aprobacion','estado'))
                grupos.setdefault(key,[]).append(g)
        nuevos.append(deepcopy(rows[inicio]))
        if not grupos:grupos={None:[None]}
        for key,amparos in grupos.items():
            pref=f'g_{seccion}_';token=f'g{amparos[0]["id"]}_' if key else f'gvacio_{seccion}_'
            for offset in (1,2,3):
                row=deepcopy(rows[inicio+offset]);cambiar(row,{pref+k:token+k for k in ('aseguradora','poliza','anexo','expedicion','aprobacion')});nuevos.append(row)
            for g in amparos:
                row=deepcopy(rows[inicio+4]);p=f'ga{g["id"]}_' if g else token
                cambiar(row,{pref+k:p+k for k in ('amparo','valor','inicio','fin')});nuevos.append(row)
                if g:
                    ver=g['estado']=='verificado'
                    for k,field in [('aseguradora','aseguradora'),('poliza','poliza'),('anexo','anexo'),('expedicion','fecha_expedicion'),('aprobacion','fecha_aprobacion')]:valores[token+k]=g[field] if ver else '[PENDIENTE]'
                    for k,field in [('amparo','amparo'),('valor','valor_formato'),('inicio','vigencia_inicio'),('fin','vigencia_fin')]:valores[p+k]=g[field] if ver else '[PENDIENTE]'
                else:
                    for k in ('aseguradora','poliza','anexo','expedicion','aprobacion','amparo','valor','inicio','fin'):valores[token+k]='[PENDIENTE]'
    nuevos+=deepcopy(rows[21:24])
    for pago in pagos or [None]:
        row=deepcopy(rows[24]);pref=f'p{pago["id"]}_' if pago else 'pvacio_'
        cambiar(row,{'p_'+k:pref+k for k in ('rubro','orden','fecha','valor')})
        for k in ('rubro','orden','fecha','valor'):valores[pref+k]=(pago['valor_formato'] if k=='valor' else pago[k] or '[PENDIENTE]') if pago else '[PENDIENTE]'
        nuevos.append(row)
    for row in rows[9:25]:tabla.remove(row)
    pos=list(tabla).index(rows[8])+1
    for row in nuevos:tabla.insert(pos,row);pos+=1
    salida=BytesIO()
    with ZipFile(BytesIO(contenido)) as a,ZipFile(salida,'w',ZIP_DEFLATED) as b:
        for info in a.infolist():b.writestr(info,E.tostring(root,encoding='utf-8',xml_declaration=True) if info.filename=='word/document.xml' else a.read(info.filename))
    return salida.getvalue(),valores
