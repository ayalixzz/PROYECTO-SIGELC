"""Recepción privada y extracción estática. No ejecuta programas ni guarda archivos."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
import re
import stat
from struct import unpack_from
from unicodedata import normalize
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from django.core.exceptions import ValidationError

from .docx_requerimientos import LIMITE, W

MAX_ARCHIVOS = 24
MAX_PAQUETE = 40 * 1024 * 1024
MAX_EXPANSION_INTERNA = 60 * 1024 * 1024
MAX_XML = 8 * 1024 * 1024
MAX_PAGINAS_PDF = 80
MAX_TEXTO = 160_000
MAX_FRAGMENTOS = 500
MAX_FRAGMENTO = 1000

S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
TIPOS = {
    'docx': ('word/document.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),
    'xlsx': ('xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),
}
TIPOS_ADMITIDOS = {'pdf', 'docx', 'doc', 'xlsx', 'zip'}
NOMBRES_WINDOWS = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
FIRMA_CFB = bytes.fromhex('D0CF11E0A1B11AE1')


class IngestaInvalida(ValidationError):
    """Archivo no apto para el circuito de extracción."""


@dataclass(frozen=True)
class ArchivoExtraido:
    nombre: str
    contenido: bytes
    sha256: str
    tipo: str
    clasificacion: str
    fragmentos: list[dict]
    advertencias: list[str]
    procedencia: dict

    @property
    def tamano(self):
        return len(self.contenido)


def _texto_normalizado(texto):
    return ''.join(c for c in normalize('NFKD', texto.casefold()) if not 0x300 <= ord(c) <= 0x36F)


_CLASIFICACION = (
    ('gcof19', r'\bgcof\s*[- ]?\s*19\b|\binforme final de (?:ejecucion y )?supervision\b'),
    ('certificacion_pagos', r'\bcertificacion (?:de )?pagos?\b|\bcertificado de pagos?\b'),
    ('factura', r'\bfactura(?: electronica| de venta)?\b'),
    ('cuenta_cobro', r'\bcuenta de cobro\b'),
    ('poliza', r'\bpoliza\b|\bgarantia de cumplimiento\b'),
    ('seguridad_social', r'\bseguridad social\b|\baportes parafiscales\b'),
    ('documento_contractual', r'\bcontrato (?:de|interadministrativo)\b|\bconvenio (?:de|interadministrativo)\b|\bacta de (?:inicio|terminacion)\b'),
    ('soporte', r'\banexo\b|\bsoporte documental\b'),
)


def _clasificar(nombre, fragmentos):
    # El título de archivo y los primeros bloques dan señales, no una clasificación legal.
    titulo = _texto_normalizado(PurePosixPath(nombre).stem.replace('_', ' ').replace('-', ' '))
    encabezado = _texto_normalizado(' '.join(p['texto'] for p in fragmentos[:8]))[:2500]
    candidatos = [etiqueta for etiqueta, patron in _CLASIFICACION if re.search(patron, titulo)]
    if not candidatos:
        candidatos = [etiqueta for etiqueta, patron in _CLASIFICACION if re.search(patron, encabezado)]
    if len(candidatos) > 1:
        return 'pendiente', 'Clasificación ambigua. El abogado debe confirmar el tipo documental.'
    if candidatos:
        return candidatos[0], None
    if not fragmentos:
        return 'pendiente', 'Sin texto extraído para confirmar el tipo documental.'
    return 'otro', None


def _ruta_segura(nombre, *, es_directorio=False):
    if not isinstance(nombre, str) or not nombre or len(nombre) > 260:
        raise IngestaInvalida('El archivo contiene un nombre vacío o demasiado largo.')
    nombre = normalize('NFC', nombre)
    if es_directorio and nombre.endswith('/'):
        nombre = nombre[:-1]
    partes = nombre.split('/')
    for parte in partes:
        if (
            not parte or parte in {'.', '..'} or parte.rstrip(' .') != parte
            or len(parte) > 180 or any(ord(c) < 32 or c in '\\:*?"<>|' for c in parte)
            or parte.split('.')[0].casefold() in NOMBRES_WINDOWS
        ):
            raise IngestaInvalida('El archivo contiene una ruta o nombre no permitido.')
    return nombre


def _entradas(paquete, *, max_entradas, max_total, max_entrada):
    entradas = paquete.infolist()
    if not entradas or len(entradas) > max_entradas:
        raise IngestaInvalida('El archivo comprimido está vacío o supera el número de entradas permitido.')
    claves = set()
    expansion = 0
    for entrada in entradas:
        nombre = _ruta_segura(entrada.orig_filename, es_directorio=entrada.is_dir())
        clave = normalize('NFKC', nombre).casefold()
        if clave in claves:
            raise IngestaInvalida('El archivo comprimido repite una ruta.')
        claves.add(clave)
        modo = stat.S_IFMT(entrada.external_attr >> 16)
        if modo not in {0, stat.S_IFREG, stat.S_IFDIR} or entrada.flag_bits & 1:
            raise IngestaInvalida('El archivo comprimido contiene enlaces, entradas especiales o cifradas.')
        if entrada.compress_type not in {ZIP_DEFLATED, ZIP_STORED}:
            raise IngestaInvalida('Método de compresión no permitido.')
        expansion += entrada.file_size
        if expansion > max_total or entrada.file_size > max_entrada:
            raise IngestaInvalida('El archivo comprimido supera el tamaño admitido al descomprimir.')
        if entrada.file_size > 1024 * 1024 and entrada.file_size > 500 * max(entrada.compress_size, 1):
            raise IngestaInvalida('El archivo comprimido supera la relación de expansión permitida.')
    return entradas


def _leer_entrada(paquete, entrada, limite):
    with paquete.open(entrada) as flujo:
        contenido = flujo.read(limite + 1)
    if len(contenido) > limite:
        raise IngestaInvalida('Un elemento del paquete excede el límite de lectura.')
    return contenido


def _xml(paquete, entrada):
    contenido = _leer_entrada(paquete, entrada, MAX_XML)
    if b'<!DOCTYPE' in contenido.upper() or b'<!ENTITY' in contenido.upper() or b'\x00' in contenido:
        raise IngestaInvalida('El documento contiene XML no admitido.')
    return ElementTree.fromstring(contenido)


def _destino_interno(origen, target):
    target = unquote(target)
    if not target or '\\' in target or urlsplit(target).scheme or target.startswith('//'):
        raise IngestaInvalida('El documento contiene referencias externas o rutas inválidas.')
    if origen == '_rels/.rels':
        raiz = ''
    else:
        raiz = origen.rsplit('/_rels/', 1)[0]
    destino = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(raiz, target))
    return _ruta_segura(destino)


class _Fragmentos:
    def __init__(self):
        self.datos = []
        self.caracteres = 0
        self.truncado = False

    def agregar(self, ubicacion, texto, **coordenadas):
        texto = texto.replace('\x00', '').strip()
        if not texto:
            return
        for inicio in range(0, len(texto), MAX_FRAGMENTO):
            bloque = texto[inicio:inicio + MAX_FRAGMENTO]
            if len(self.datos) >= MAX_FRAGMENTOS or self.caracteres + len(bloque) > MAX_TEXTO:
                self.truncado = True
                return
            etiqueta = f'{ubicacion} · fragmento {inicio // MAX_FRAGMENTO + 1}' if len(texto) > MAX_FRAGMENTO else ubicacion
            self.caracteres += len(bloque)
            self.datos.append({'ubicacion': etiqueta, 'texto': bloque, **coordenadas})


def _validar_ooxml(paquete, tipo):
    entradas = _entradas(paquete, max_entradas=650, max_total=MAX_EXPANSION_INTERNA, max_entrada=LIMITE)
    nombres = {entrada.filename for entrada in entradas}
    principal, mime = TIPOS[tipo]
    if not {principal, '[Content_Types].xml', '_rels/.rels'} <= nombres:
        raise IngestaInvalida('El contenido no corresponde al formato Office declarado.')
    tipos = _xml(paquete, '[Content_Types].xml')
    if not any(n.get('PartName') == f'/{principal}' and n.get('ContentType') == mime for n in tipos):
        raise IngestaInvalida('El contenido principal Office no coincide con la extensión.')
    for n in tipos:
        ct = n.get('ContentType', '').lower()
        if any(bloqueado in ct for bloqueado in ('macroenabled', 'vbaproject', 'activex', 'oleobject')):
            raise IngestaInvalida('El documento contiene macros u objetos activos.')
    partes = {}
    for entrada in entradas:
        nombre = entrada.filename
        clave = nombre.lower()
        if any(v in f'/{clave}' for v in (
            '/vbaproject', '/activex/', '/embeddings/', '/externallinks/', '/macrosheets/',
            '/webextensions/', '/customui/', '/connections.xml',
        )):
            raise IngestaInvalida('El documento contiene macros, objetos, vínculos o conexiones no permitidos.')
        if clave.endswith('.rels'):
            raiz = _xml(paquete, entrada)
            for rel in raiz:
                if rel.get('TargetMode', '').lower() == 'external':
                    raise IngestaInvalida('Elimine las relaciones externas del documento antes de recibirlo.')
                ref = _destino_interno(nombre, rel.get('Target', ''))
                if ref not in nombres:
                    raise IngestaInvalida('El documento contiene una relación a una parte inexistente.')
            partes[nombre] = raiz
    relaciones = partes['_rels/.rels']
    if not any(
        n.get('Type', '').endswith('/officeDocument')
        and _destino_interno('_rels/.rels', n.get('Target', '')) == principal for n in relaciones
    ):
        raise IngestaInvalida('Falta la relación al documento Office principal.')
    return partes, nombres


def _docx(paquete, nombres, fragmentos):
    partes = [n for n in sorted(nombres) if re.fullmatch(r'word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml', n)]
    for nombre in partes:
        raiz = _xml(paquete, nombre)
        principal = nombre == 'word/document.xml'
        seccion = 'Cuerpo' if principal else PurePosixPath(nombre).stem
        contenedor = raiz.find(W + 'body') if principal else raiz
        if contenedor is None or (principal and raiz.tag != W + 'document'):
            raise IngestaInvalida('El DOCX no contiene un cuerpo válido.')
        if any(r.tag in {W + 'altChunk', W + 'object'} for r in contenedor.iter()):
            raise IngestaInvalida('El DOCX contiene contenido alternativo incrustado.')
        campos = [r.get(W + 'instr', '') for r in contenedor.iter(W + 'fldSimple')]
        campos.append(''.join(r.text or '' for r in contenedor.iter(W + 'instrText')))
        if any(re.search(r'\b(?:DDE(?:AUTO)?|INCLUDETEXT|INCLUDEPICTURE|DATABASE)\b', campo, re.IGNORECASE) for campo in campos):
            raise IngestaInvalida('El DOCX contiene campos que pueden consultar contenido externo.')
        padres = {hijo: padre for padre in contenedor.iter() for hijo in padre}
        celdas = {}
        for tabla, nodo in enumerate(contenedor.iter(W + 'tbl'), 1):
            for fila, r in enumerate(nodo.findall(W + 'tr'), 1):
                for celda, c in enumerate(r.findall(W + 'tc'), 1):
                    celdas[c] = (tabla, fila, celda)
        for indice, parrafo in enumerate(contenedor.iter(W + 'p'), 1):
            texto = ''.join(n.text or '' for n in parrafo.iter(W + 't'))
            padre = padres.get(parrafo)
            while padre is not None and padre not in celdas:
                padre = padres.get(padre)
            ubicacion = f'{seccion} · párrafo {indice}'
            datos = {'seccion': seccion, 'parrafo': indice}
            if padre is not None:
                tabla, fila, celda = celdas[padre]
                ubicacion += f' · tabla {tabla}, fila {fila}, celda {celda}'
                datos.update(tabla=tabla, fila=fila, celda=celda)
            fragmentos.agregar(ubicacion, texto, **datos)
            if fragmentos.truncado:
                return


def _xlsx(paquete, partes, nombres, fragmentos):
    libro = _xml(paquete, 'xl/workbook.xml')
    if libro.tag != S + 'workbook':
        raise IngestaInvalida('El XLSX no contiene un libro válido.')
    cadena = []
    if 'xl/sharedStrings.xml' in nombres:
        cadena = [''.join(n.text or '' for n in item.iter(S + 't')) for item in _xml(paquete, 'xl/sharedStrings.xml')]
    relaciones = partes.get('xl/_rels/workbook.xml.rels')
    if relaciones is None:
        raise IngestaInvalida('El XLSX no contiene referencias de hojas.')
    rutas = {
        rel.get('Id'): _destino_interno('xl/_rels/workbook.xml.rels', rel.get('Target', ''))
        for rel in relaciones if rel.get('Type', '').endswith('/worksheet')
    }
    hojas = list(libro.iter(S + 'sheet'))
    if not hojas or len(hojas) > 40:
        raise IngestaInvalida('El XLSX no contiene hojas admitidas o supera el límite.')
    hubo_formula = False
    for ordinal, hoja in enumerate(hojas, 1):
        nombre = hoja.get('name', f'Hoja {ordinal}')
        ruta = rutas.get(hoja.get(R + 'id'))
        if not ruta:
            raise IngestaInvalida('El XLSX refiere una hoja no admitida.')
        for celda in _xml(paquete, ruta).iter(S + 'c'):
            coordenada = celda.get('r', '')
            if not re.fullmatch(r'[A-Z]{1,3}[1-9]\d{0,6}', coordenada):
                raise IngestaInvalida('El XLSX contiene coordenadas de celda inválidas.')
            valor = celda.findtext(S + 'v', default='')
            tipo = celda.get('t', '')
            formula = celda.find(S + 'f') is not None
            hubo_formula |= formula
            if tipo == 'inlineStr':
                valor = ''.join(n.text or '' for n in celda.iter(S + 't'))
            elif tipo == 's':
                if not valor.isdigit() or int(valor) >= len(cadena):
                    raise IngestaInvalida('Celda XLSX con texto compartido inválido.')
                valor = cadena[int(valor)]
            if formula and not valor:
                continue
            fragmentos.agregar(
                f'Hoja {ordinal} «{nombre}» · celda {coordenada}', valor,
                hoja=nombre, fila=int(re.search(r'\d+$', coordenada)[0]),
                celda=coordenada, formula_cache=formula,
            )
            if fragmentos.truncado:
                return hubo_formula
    return hubo_formula


def _pdf(contenido, fragmentos):
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import PyPdfError

    if not re.match(rb'%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n)', contenido) or b'%%EOF' not in contenido[-2048:]:
        raise IngestaInvalida('El contenido no corresponde a un PDF admitido.')
    sin_texto = []
    try:
        # La configuración se propaga solo en el contexto de esta lectura.
        with apply_configuration(
            maximum_declared_stream_length=16_000_000,
            zlib_maximum_output_length=8_000_000,
            zlib_maximum_recovery_input_length=1_000_000,
            array_based_stream_maximum_output_length=8_000_000,
            lzw_maximum_output_length=8_000_000,
            run_length_maximum_output_length=8_000_000,
            stream_decoding_work_maximum_length=24_000_000,
            stream_parsing_work_maximum_count=200_000,
            page_tree_maximum_page_entries=MAX_PAGINAS_PDF,
            page_tree_maximum_depth=30,
            xform_maximum_invocations_per_extraction=250,
            jbig2dec_binary=None,
        ):
            lector = PdfReader(BytesIO(contenido), strict=True)
            if lector.is_encrypted:
                raise IngestaInvalida('PDF protegido o cifrado. Entregue una copia habilitada para revisión.')
            if not 1 <= len(lector.pages) <= MAX_PAGINAS_PDF:
                raise IngestaInvalida('El PDF está vacío o supera el límite de páginas.')
            catalogo = lector.root_object
            nombres = catalogo.get('/Names')
            nombres = nombres.get_object() if nombres is not None else {}
            if (any(atributo in catalogo for atributo in ('/OpenAction', '/AA', '/AF'))
                    or any(atributo in nombres for atributo in ('/JavaScript', '/EmbeddedFiles'))):
                raise IngestaInvalida('El PDF contiene acciones automáticas o adjuntos internos.')
            for numero, pagina in enumerate(lector.pages, 1):
                if '/AA' in pagina:
                    raise IngestaInvalida('El PDF contiene acciones automáticas de página.')
                anotaciones = pagina.get('/Annots')
                if anotaciones is not None:
                    for referencia in anotaciones.get_object():
                        anotacion = referencia.get_object()
                        if any(v in anotacion for v in ('/AA', '/FS')) or anotacion.get('/Subtype') in {
                            '/FileAttachment', '/RichMedia', '/Screen', '/Movie', '/Sound', '/3D',
                        }:
                            raise IngestaInvalida('El PDF contiene una anotación activa o un archivo incrustado.')
                        accion = anotacion.get('/A')
                        if accion is not None and accion.get_object().get('/S') != '/GoTo':
                            raise IngestaInvalida('El PDF contiene una acción o enlace externo.')
                contexto = pagina.get_contents()
                if contexto is not None and len(contexto.get_data()) > 2_000_000:
                    raise IngestaInvalida('Una página del PDF excede el límite de texto procesable.')
                texto = pagina.extract_text()
                if not texto.strip():
                    sin_texto.append(numero)
                fragmentos.agregar(f'Página {numero}', texto, pagina=numero)
                if fragmentos.truncado:
                    break
    except (PyPdfError, ValueError, TypeError, AttributeError, NotImplementedError, RecursionError) as error:
        raise IngestaInvalida('El PDF no se pudo leer de forma segura.') from error
    return sin_texto


def _validar_doc(contenido):
    # Se inspecciona el directorio CFB. No se abre con Word, LibreOffice ni macros.
    if len(contenido) < 512 or contenido[:8] != FIRMA_CFB:
        raise IngestaInvalida('El contenido no corresponde a un archivo DOC binario.')
    mayor, orden, bits = unpack_from('<HHH', contenido, 26)
    if orden != 0xfffe or (mayor, bits) not in {(3, 9), (4, 12)}:
        raise IngestaInvalida('El contenedor del DOC no es válido.')
    sector_bytes = 1 << bits
    if len(contenido) % sector_bytes:
        raise IngestaInvalida('El DOC binario contiene sectores incompletos.')
    n_sectores = len(contenido) // sector_bytes - 1
    n_fat, dir_inicial = unpack_from('<II', contenido, 44)
    difat_inicial, n_difat = unpack_from('<II', contenido, 68)
    final = 0xfffffffe
    libre = 0xffffffff

    def sector(i):
        if not 0 <= i < n_sectores:
            raise IngestaInvalida('Referencia fuera del contenedor DOC.')
        inicio = (i + 1) * sector_bytes
        return contenido[inicio:inicio + sector_bytes]

    if not 1 <= n_fat <= min(350, n_sectores) or n_difat > 32:
        raise IngestaInvalida('Tabla de asignación DOC fuera de los límites.')
    ids_fat = [i for i in unpack_from('<109I', contenido, 76) if i != libre]
    visitados = set()
    for _ in range(n_difat):
        if difat_inicial in visitados:
            raise IngestaInvalida('Directorio de asignación DOC cíclico.')
        visitados.add(difat_inicial)
        celdas = unpack_from(f'<{sector_bytes // 4}I', sector(difat_inicial))
        ids_fat.extend(i for i in celdas[:-1] if i != libre)
        difat_inicial = celdas[-1]
    if len(ids_fat) != n_fat or len(set(ids_fat)) != n_fat or (n_difat and difat_inicial != final):
        raise IngestaInvalida('Directorio de asignación DOC inválido.')
    fat = [i for ref in ids_fat for i in unpack_from(f'<{sector_bytes // 4}I', sector(ref))]
    directorio = set()
    streams = {}
    raiz = None
    while dir_inicial != final:
        if dir_inicial in directorio or len(directorio) >= 256 or dir_inicial >= len(fat):
            raise IngestaInvalida('Directorio DOC inválido o demasiado grande.')
        directorio.add(dir_inicial)
        datos = sector(dir_inicial)
        for i in range(0, sector_bytes, 128):
            longitud, clase = unpack_from('<HB', datos, i + 64)
            if clase == 0:
                continue
            if clase not in {1, 2, 5} or longitud not in range(2, 65, 2):
                raise IngestaInvalida('Entrada no admitida en el directorio DOC.')
            nombre = datos[i:i + longitud - 2].decode('utf-16-le').casefold()
            if nombre in {'macros', 'vba', '_vba_project', '_vba_project_cur', 'objectpool', 'encryptedpackage', 'encryptioninfo'} or nombre.startswith('__srp_'):
                raise IngestaInvalida('El DOC contiene macros, objetos OLE o datos cifrados.')
            inicio = unpack_from('<I', datos, i + 116)[0]
            tamano = unpack_from('<Q' if mayor == 4 else '<I', datos, i + 120)[0]
            if clase == 5:
                if raiz is not None:
                    raise IngestaInvalida('El contenedor DOC tiene varias entradas raíz.')
                raiz = (inicio, tamano)
            if clase == 2:
                if nombre in {'worddocument', '0table', '1table'} and nombre in streams:
                    raise IngestaInvalida('El DOC contiene flujos Word duplicados.')
                streams[nombre] = (inicio, tamano)
        dir_inicial = fat[dir_inicial]
    if raiz is None or 'worddocument' not in streams:
        raise IngestaInvalida('El contenedor binario no corresponde a un documento Word DOC.')
    inicio, tamano = streams['worddocument']
    if tamano < 32 or tamano > LIMITE:
        raise IngestaInvalida('El flujo Word principal tiene un tamaño inválido.')
    if tamano < 4096:
        # WordDocument puede comenzar en un minisector del flujo de la raíz.
        inicio_raiz, tamano_raiz = raiz
        offset = inicio * 64
        if offset + 32 > tamano_raiz or tamano_raiz > LIMITE:
            raise IngestaInvalida('Minisector Word fuera de los límites del contenedor.')
        salto, dentro = divmod(offset, sector_bytes)
        vistos = {inicio_raiz}
        for _ in range(salto):
            sector(inicio_raiz)
            if inicio_raiz >= len(fat) or fat[inicio_raiz] in vistos:
                raise IngestaInvalida('Cadena de sectores Word inválida.')
            inicio_raiz = fat[inicio_raiz]
            vistos.add(inicio_raiz)
        fib = sector(inicio_raiz)[dentro:dentro + 32]
    else:
        fib = sector(inicio)[:32]
    firma = unpack_from('<H', fib)[0]
    banderas = unpack_from('<H', fib, 10)[0]
    tabla = '1table' if banderas & 0x0200 else '0table'
    if firma != 0xa5ec or tabla not in streams or not streams[tabla][1]:
        raise IngestaInvalida('El contenedor binario no corresponde a un documento Word DOC.')
    if banderas & 0x8100:
        raise IngestaInvalida('El DOC Word está protegido o cifrado.')


def _analizar(nombre, contenido, tipo, procedencia):
    fragmentos = _Fragmentos()
    advertencias = []
    if tipo == 'doc':
        _validar_doc(contenido)
        advertencias.append('DOC binario conservado sin abrir. Convierta una copia a DOCX o PDF para extraer evidencia.')
    elif tipo == 'pdf':
        sin_texto = _pdf(contenido, fragmentos)
        if sin_texto:
            advertencias.append('PDF sin texto seleccionable en páginas ' + ', '.join(map(str, sin_texto)) + '. Requiere OCR o revisión manual.')
    else:
        with ZipFile(BytesIO(contenido)) as paquete:
            partes, nombres = _validar_ooxml(paquete, tipo)
            if tipo == 'docx':
                _docx(paquete, nombres, fragmentos)
            elif _xlsx(paquete, partes, nombres, fragmentos):
                advertencias.append('Las fórmulas no se ejecutaron. Los resultados en caché requieren cotejo.')
    if fragmentos.truncado:
        advertencias.append('La extracción alcanzó el límite de fragmentos o caracteres. Revise el original para completar la evidencia.')
    if not fragmentos.datos and tipo not in {'doc', 'pdf'}:
        advertencias.append('El documento no contiene texto compatible con la extracción disponible.')
    clasificacion, aviso = _clasificar(nombre, fragmentos.datos)
    if aviso:
        advertencias.append(aviso)
    return ArchivoExtraido(nombre, contenido, sha256(contenido).hexdigest(), tipo, clasificacion,
                           fragmentos.datos, advertencias, procedencia)


def recibir_paquete(archivos):
    """Verifica todos los originales antes de devolver resultados, sin escribir en disco o base."""
    originales = list(archivos)
    if not 1 <= len(originales) <= MAX_ARCHIVOS:
        raise IngestaInvalida(f'Seleccione entre 1 y {MAX_ARCHIVOS} archivos.')
    resultado = []
    suma = 0
    documentos = 0

    def agregar(nombre, contenido, procedencia):
        nonlocal documentos, suma
        tipo = PurePosixPath(nombre).suffix.lower().lstrip('.')
        if tipo not in TIPOS_ADMITIDOS - {'zip'}:
            raise IngestaInvalida('Seleccione PDF, DOCX, DOC, XLSX o un ZIP con estos formatos.')
        documentos += 1
        suma += len(contenido)
        if documentos > MAX_ARCHIVOS or suma > MAX_PAQUETE:
            raise IngestaInvalida('El paquete supera la cantidad o el tamaño total permitido.')
        resultado.append(_analizar(nombre, contenido, tipo, procedencia))

    for archivo in originales:
        nombre_original = _ruta_segura(archivo.name)
        if '/' in nombre_original:
            raise IngestaInvalida('Cargue el nombre del archivo sin una ruta.')
        try:
            contenido = archivo.read(LIMITE + 1)
            if not contenido or len(contenido) > LIMITE:
                raise IngestaInvalida('El archivo está vacío o excede el límite individual de 20 MiB.')
            if PurePosixPath(nombre_original).suffix.lower() != '.zip':
                agregar(nombre_original, contenido, {'origen': 'carga_directa'})
                continue
            digest = sha256(contenido).hexdigest()
            suma += len(contenido)
            if suma > MAX_PAQUETE:
                raise IngestaInvalida('El paquete supera el tamaño total permitido.')
            with ZipFile(BytesIO(contenido)) as paquete:
                entradas = _entradas(paquete, max_entradas=MAX_ARCHIVOS * 2, max_total=MAX_PAQUETE, max_entrada=LIMITE)
                miembros = [e for e in entradas if not e.is_dir()]
                if not miembros or documentos + len(miembros) > MAX_ARCHIVOS:
                    raise IngestaInvalida('El ZIP no contiene documentos admitidos o supera el número máximo.')
                resultado.append(ArchivoExtraido(nombre_original, contenido, digest, 'zip', 'paquete', [], [],
                                                {'origen': 'carga_directa'}))
                for miembro in miembros:
                    if PurePosixPath(miembro.filename).suffix.lower() == '.zip':
                        raise IngestaInvalida('No se permiten ZIP anidados.')
                    miembro_nombre = normalize('NFC', miembro.filename)
                    datos = _leer_entrada(paquete, miembro, LIMITE)
                    if not datos:
                        raise IngestaInvalida('El ZIP contiene un documento vacío.')
                    agregar(PurePosixPath(miembro_nombre).name, datos, {
                        'origen': 'zip', 'paquete': nombre_original, 'paquete_sha256': digest, 'ruta': miembro_nombre,
                    })
        except (BadZipFile, ElementTree.ParseError, UnicodeError, NotImplementedError, OSError, RuntimeError, ValueError, ImportError) as error:
            raise IngestaInvalida(f'No se pudo verificar el contenido de «{nombre_original}».') from error
    return resultado
