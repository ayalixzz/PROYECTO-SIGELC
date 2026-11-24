import re
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from xml.dom import minidom
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError


LIMITE = 20 * 1024 * 1024
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
PARTE = re.compile(r'word/(?:document|header\d+|footer\d+)\.xml')
MARCADOR = re.compile(r'\{\{([a-z_][a-z_0-9]*)\}\}')


def leer_docx(archivo):
    if not archivo.name.lower().endswith('.docx'):
        raise ValidationError('Cargue un DOCX editable. Los PDF requieren una plantilla DOCX autorizada.')
    with archivo.open('rb') as entrada:
        contenido = entrada.read(LIMITE + 1)
    if len(contenido) > LIMITE:
        raise ValidationError('El DOCX supera el límite de 20 MiB.')
    return contenido


def _xml(contenido):
    if b'<!DOCTYPE' in contenido.upper() or b'<!ENTITY' in contenido.upper() or b'\x00' in contenido:
        raise ValidationError('El DOCX contiene XML no admitido.')
    return ET.fromstring(contenido)


def procesar_docx(contenido, datos=None, *, rotulo=None):
    """Valida un DOCX y, si recibe datos, rellena sus marcadores de texto."""
    salida = BytesIO()
    vistos = set()
    pendientes = set()
    try:
        with ZipFile(BytesIO(contenido)) as paquete, ZipFile(salida, 'w', ZIP_DEFLATED) as resultado:
            entradas = paquete.infolist()
            nombres = {parte.filename for parte in entradas}
            requeridos = {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'}
            if not requeridos <= nombres or len(entradas) != len(nombres) or len(entradas) > 1000:
                raise ValidationError('El archivo no tiene la estructura de un DOCX admitido.')
            if sum(parte.file_size for parte in entradas) > 4 * LIMITE:
                raise ValidationError('El contenido expandido del DOCX supera el límite.')
            tipos = _xml(paquete.read('[Content_Types].xml'))
            principal = next((n.attrib.get('ContentType') for n in tipos if n.attrib.get('PartName') == '/word/document.xml'), None)
            if principal != 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml':
                raise ValidationError('Se requiere un documento Word DOCX sin macros.')
            for nombre in sorted(nombres):
                ruta = PurePosixPath(nombre)
                if ruta.is_absolute() or '..' in ruta.parts or '\\' in nombre or ':' in nombre:
                    raise ValidationError('El DOCX contiene una ruta interna inválida.')
                if any(x in nombre.lower() for x in ('vbaproject', 'word/activex/', 'word/embeddings/')):
                    raise ValidationError('El DOCX contiene macros u objetos incrustados.')
                contenido_parte = paquete.read(nombre)
                if nombre.endswith(('.xml', '.rels')):
                    raiz = _xml(contenido_parte)
                    if nombre.endswith('.rels') and any(n.attrib.get('TargetMode', '').lower() == 'external' for n in raiz):
                        raise ValidationError('Elimine los vínculos externos del DOCX antes de cargarlo.')
                    if nombre == 'word/document.xml' and (raiz.tag != W + 'document' or raiz.find(W + 'body') is None):
                        raise ValidationError('El DOCX carece de un cuerpo válido.')
                    if PARTE.fullmatch(nombre):
                        documento = minidom.parseString(contenido_parte)
                        espacio_word = W[1:-1]
                        modificado = False
                        for parrafo in list(documento.getElementsByTagNameNS(espacio_word, 'p')):
                            nodos = list(parrafo.getElementsByTagNameNS(espacio_word, 't'))
                            texto = ''.join(_texto(n) for n in nodos)
                            lista = MARCADOR.fullmatch(texto.strip())
                            if datos is not None and lista and isinstance(datos.get(lista[1]), list) and datos[lista[1]]:
                                vistos.add(lista[1])
                                for item in datos[lista[1]]:
                                    copia = parrafo.cloneNode(True)
                                    nuevos = list(copia.getElementsByTagNameNS(espacio_word, 't'))
                                    inicio_lista = texto.index(lista[0])
                                    _reemplazar(nuevos, inicio_lista, inicio_lista + len(lista[0]), str(item))
                                    for nuevo in nuevos:
                                        _insertar_saltos(nuevo)
                                    parrafo.parentNode.insertBefore(copia, parrafo)
                                parrafo.parentNode.removeChild(parrafo)
                                modificado = True
                                continue
                            for marca in reversed(list(MARCADOR.finditer(texto))):
                                clave = marca[1]
                                vistos.add(clave)
                                if datos is not None:
                                    valor = datos.get(clave)
                                    if valor is None or valor == '' or valor == []:
                                        pendientes.add(clave)
                                        valor = f'[PENDIENTE: {clave}]'
                                    _reemplazar(nodos, marca.start(), marca.end(), str(valor))
                                    modificado = True
                            if datos is not None:
                                for nodo in nodos:
                                    _insertar_saltos(nodo)
                        if rotulo and nombre == 'word/document.xml':
                            cuerpo = documento.getElementsByTagNameNS(espacio_word, 'body')[0]
                            prefijo = cuerpo.prefix
                            def elemento(nombre):
                                return documento.createElementNS(espacio_word, f'{prefijo}:{nombre}' if prefijo else nombre)
                            parrafo, run, texto_rotulo = (elemento(nombre) for nombre in ('p', 'r', 't'))
                            texto_rotulo.appendChild(documento.createTextNode(rotulo))
                            run.appendChild(texto_rotulo)
                            parrafo.appendChild(run)
                            primer_contenido = next((n for n in cuerpo.childNodes if n.nodeType == n.ELEMENT_NODE), None)
                            if primer_contenido is None or rotulo not in _texto_visible(primer_contenido, espacio_word):
                                cuerpo.insertBefore(parrafo, cuerpo.firstChild)
                                modificado = True
                        if modificado:
                            # Se conservan las partes del paquete y los nodos de formato.
                            contenido_parte = documento.toxml(encoding='utf-8')
                if datos is not None or rotulo:
                    resultado.writestr(nombre, contenido_parte)
    except (BadZipFile, ET.ParseError, RuntimeError, NotImplementedError, OSError, ValueError) as error:
        raise ValidationError('El archivo DOCX no se pudo leer.') from error
    return salida.getvalue() if datos is not None or rotulo else contenido, vistos, sorted(pendientes)


def _texto_visible(elemento, espacio):
    return ''.join(_texto(n) for n in elemento.getElementsByTagNameNS(espacio, 't'))


def _texto(nodo):
    return ''.join(hijo.data for hijo in nodo.childNodes if hijo.nodeType in (hijo.TEXT_NODE, hijo.CDATA_SECTION_NODE))


def _reemplazar(nodos, inicio, fin, valor):
    posicion = 0
    for nodo in nodos:
        texto = _texto(nodo)
        siguiente = posicion + len(texto)
        if posicion < fin and siguiente > inicio:
            prefijo = texto[:max(0, inicio - posicion)]
            sufijo = texto[max(0, fin - posicion):]
            while nodo.firstChild:
                nodo.removeChild(nodo.firstChild)
            nodo.appendChild(nodo.ownerDocument.createTextNode(prefijo + (valor if posicion <= inicio else '') + sufijo))
            nodo.setAttributeNS('http://www.w3.org/XML/1998/namespace', 'xml:space', 'preserve')
        posicion = siguiente


def _insertar_saltos(nodo):
    primera, *lineas = _texto(nodo).split('\n')
    if not lineas:
        return
    while nodo.firstChild:
        nodo.removeChild(nodo.firstChild)
    nodo.appendChild(nodo.ownerDocument.createTextNode(primera))
    referencia = nodo.nextSibling
    for linea in lineas:
        salto = nodo.ownerDocument.createElementNS(nodo.namespaceURI, f'{nodo.prefix}:br' if nodo.prefix else 'br')
        texto_nuevo = nodo.cloneNode(False)
        texto_nuevo.appendChild(nodo.ownerDocument.createTextNode(linea))
        nodo.parentNode.insertBefore(salto, referencia)
        nodo.parentNode.insertBefore(texto_nuevo, referencia)
