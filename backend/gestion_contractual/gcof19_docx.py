"""Adaptación de filas de la plantilla aprobada; no altera el motor de texto."""
from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as E
from zipfile import ZipFile, ZIP_DEFLATED

from django.core.exceptions import ValidationError

from .docx_requerimientos import procesar_docx

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def validar_plantilla(contenido):
    _, marcadores, _ = procesar_docx(contenido)
    if not {'rotulo_gcof19', 'grupo_obligaciones', 'n1', 'd1', 'a1', 'pr1', 'o1', 'pc1'} <= marcadores:
        raise ValidationError('La plantilla no contiene la estructura GCOF19 aprobada.')
    with ZipFile(BytesIO(contenido)) as z:
        root = E.fromstring(z.read('word/document.xml'))
    tablas = [t for t in root.iter(W+'tbl') if '{{grupo_obligaciones}}' in ''.join(t.itertext())]
    if len(tablas) != 1:
        raise ValidationError('Debe existir una única tabla patrón de obligaciones GCOF19.')
    rows = tablas[0].findall(W+'tr')
    if len(rows) != 5 or any(len(row.findall(W+'tc')) != 6 for row in rows[1:]):
        raise ValidationError('La tabla patrón GCOF19 no conserva las seis columnas y tres filas de la plantilla aprobada.')
    return root, tablas[0]


def tablas_obligaciones(contenido, obligaciones):
    root, tabla = validar_plantilla(contenido)
    rows = tabla.findall(W+'tr')
    parent = next(p for p in root.iter() if tabla in list(p))
    index = list(parent).index(tabla)
    parent.remove(tabla)
    grupos = [(tipo, [o for o in obligaciones if o['tipo'] == tipo]) for tipo in ('general', 'especifica')]
    if not obligaciones: grupos = [('pendiente', [None])]
    for tipo, fichas in grupos:
        if not fichas: continue
        nueva = deepcopy(tabla)
        for row in nueva.findall(W+'tr'): nueva.remove(row)
        titulo = deepcopy(rows[0])
        for t in titulo.iter(W+'t'):
            if '{{grupo_obligaciones}}' in (t.text or ''):
                t.text = t.text.replace('{{grupo_obligaciones}}', {'general':'OBLIGACIONES GENERALES', 'especifica':'OBLIGACIONES ESPECÍFICAS', 'pendiente':'OBLIGACIONES PENDIENTES DE REGISTRO'}[tipo])
        nueva.append(titulo); nueva.append(deepcopy(rows[1]))
        for ob in fichas:
            row = deepcopy(rows[2])
            alias = ['n','d','a','pr','o','pc']
            for col, clave in zip(row.findall(W+'tc'), alias):
                for t in col.iter(W+'t'):
                    if (t.text or '') == '{{'+clave+'1}}':
                        t.text = '{{ob_'+str(ob['id'])+'_'+clave+'}}' if ob else '[PENDIENTE]'
            nueva.append(row)
        # Word/LibreOffice pueden fusionar tablas contiguas y perder la cabecera
        # repetida del segundo bloque. Separador de 1 pt, sin cambiar las tablas.
        if index and parent[index-1].tag == W+'tbl':
            parrafo = E.Element(W+'p')
            propiedades = E.SubElement(parrafo,W+'pPr')
            E.SubElement(propiedades,W+'spacing',{W+'before':'0',W+'after':'0',W+'line':'20',W+'lineRule':'exact'})
            parent.insert(index,parrafo);index += 1
        parent.insert(index, nueva);index += 1
    salida = BytesIO()
    with ZipFile(BytesIO(contenido)) as original, ZipFile(salida, 'w', ZIP_DEFLATED) as z:
        for info in original.infolist():
            z.writestr(info, E.tostring(root, encoding='utf-8', xml_declaration=True) if info.filename == 'word/document.xml' else original.read(info.filename))
    return salida.getvalue()
