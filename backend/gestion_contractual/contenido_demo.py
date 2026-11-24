"""Contenido ficticio reproducible para la demostración portable."""
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ESPACIO_WORD = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
TIPO_WORD = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
ROTULO = 'SIGELC · DEMOSTRACIÓN FICTICIA'
TITULOS = {
    'primer-requerimiento': 'Primer requerimiento de demostración',
    'segundo-requerimiento': 'Segundo requerimiento de demostración',
    'tercer-requerimiento': 'Tercer requerimiento de demostración',
    'observaciones': 'Acta de observaciones de demostración',
}


def _zip(partes):
    salida = BytesIO()
    with ZipFile(salida, 'w') as paquete:
        for ruta, datos in partes.items():
            entrada = ZipInfo(ruta, date_time=(2026, 1, 1, 0, 0, 0))
            paquete.writestr(entrada, datos, compress_type=ZIP_DEFLATED)
    return salida.getvalue()


def _parrafo(texto, estilo=''):
    formato = f'<w:pPr><w:pStyle w:val="{estilo}"/></w:pPr>' if estilo else ''
    return f'<w:p>{formato}<w:r><w:t xml:space="preserve">{escape(texto)}</w:t></w:r></w:p>'


def _word(titulo, contenido):
    tipos = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{TIPO_WORD}"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>'
    )
    espacio_rel = 'http://schemas.openxmlformats.org/package/2006/relationships'
    tipo_rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    relaciones = (
        f'<Relationships xmlns="{espacio_rel}"><Relationship Id="rId1" '
        f'Target="word/document.xml" Type="{tipo_rel}/officeDocument"/></Relationships>'
    )
    estilos_rel = (
        f'<Relationships xmlns="{espacio_rel}"><Relationship Id="rId1" '
        f'Target="styles.xml" Type="{tipo_rel}/styles"/></Relationships>'
    )
    estilos = (
        f'<w:styles xmlns:w="{ESPACIO_WORD}"><w:style w:type="paragraph" w:styleId="TituloDemo">'
        '<w:name w:val="Título de la demostración"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
        '</w:style></w:styles>'
    )
    parrafos = _parrafo(ROTULO) + _parrafo(titulo, 'TituloDemo')
    parrafos += ''.join(_parrafo(linea) for linea in contenido)
    cuerpo = f'<w:document xmlns:w="{ESPACIO_WORD}"><w:body>{parrafos}</w:body></w:document>'
    return _zip({
        '[Content_Types].xml': tipos,
        '_rels/.rels': relaciones,
        'word/document.xml': cuerpo,
        'word/_rels/document.xml.rels': estilos_rel,
        'word/styles.xml': estilos,
    })


def plantilla_ficticia(tipo):
    """Devuelve el nombre y el DOCX de una plantilla sin insumos institucionales."""
    titulo = TITULOS[tipo]
    ficha = (
        'Entidad: {{entidad}}',
        'Número de contrato: {{numero_contrato}}',
        'Vigencia contractual: {{vigencia}}',
        'Contratista: {{contratista}}',
        'Objeto: {{objeto}}',
        'Supervisor: {{supervisor}}',
    )
    if tipo == 'observaciones':
        contenido = (
            '{{rotulo_borrador}}', *ficha, 'Fecha de la revisión: {{fecha_revision}}',
            'Observaciones documentales', '{{observaciones_documentales}}',
        )
    else:
        contenido = (
            *ficha, 'Fecha de elaboración: {{fecha_elaboracion}}',
            'Plazo de referencia: {{plazo_dias_habiles}} días hábiles',
            'Fecha de vencimiento: {{fecha_vencimiento}}',
            'Documentos y aclaraciones solicitados', '{{documentos_solicitados}}',
        )
    return f'sigelc-plantilla-ficticia-{tipo}.docx', _word(titulo, contenido)


def paquetes_ficticios():
    """Crea dos entregas comparables para el expediente ficticio SIGELC-DEMO-001."""
    contratista = 'Consorcio ficticio Horizonte'
    anexo = _word('Anexo de identificación', (
        'Número de contrato: SIGELC-DEMO-001',
        f'Contratista: {contratista}',
        'Objeto: Inventario técnico de espacios comunitarios',
    ))
    for entrega, caso, numero in (
        ('01-inicial', 'Entrega inicial', 'SIGELC-DEMO-999'),
        ('02-corregida', 'Entrega corregida', 'SIGELC-DEMO-001'),
    ):
        informe = _word('Informe de supervisión GCOF19 ficticio', (
            caso,
            f'Número de contrato: {numero}',
            f'Contratista: {contratista}',
            'Valor actualizado: COP 12.500.000,00',
        ))
        yield f'paquete-ficticio-{entrega}.zip', _zip({
            'supervision/Informe GCOF19.docx': informe,
            'soportes/Anexo de identificación.docx': anexo,
        })
