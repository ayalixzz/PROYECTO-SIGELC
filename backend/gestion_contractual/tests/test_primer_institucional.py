from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.models import Auditoria, DocumentoGenerado, RequisitoDocumental, TipoDocumental
from gestion_contractual.primer_requerimiento import preparar_primer_requerimiento
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.services import cambiar_estado, iniciar_seguimiento
from . import test_requerimientos


class PrimerInstitucionalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        from .calendarios_ficticios import aprobar_regresion
        aprobar_regresion(cls.abogado)
    setUp = test_requerimientos.RequerimientosTest.setUp

    def preparar(self, iniciar=True):
        archivo = next((Path(settings.BASE_DIR).parent / 'plantillas_institucionales/v1/preparadas').glob('01-*.docx'))
        self.original = archivo.read_bytes()
        self.plantilla = registrar_plantilla(usuario=self.abogado, tipo=test_requerimientos.TIPO,
            nombre='Institucional primer requerimiento v1', archivo=SimpleUploadedFile(archivo.name, self.original), activar=True)
        self.contrato.fecha_asignacion = date(2026, 1, 2)
        self.contrato.fecha_terminacion = date(2025, 12, 31)
        self.contrato.supervisor_nombre = 'Supervisor FICTICIO'
        self.contrato.save()
        if iniciar:
            self.etapa = iniciar_seguimiento(contrato_id=self.contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True)

    def generar(self, **opciones):
        return generar_requerimiento(usuario=self.abogado, contrato_id=self.contrato.pk,
            tipo=test_requerimientos.TIPO, **opciones)

    def test_fuentes_plazo_pendientes_y_datos_ejemplo_excluidos(self):
        self.preparar()
        generado = self.generar()
        self.assertEqual(generado.datos_utilizados['actuacion_origen']['id'], self.etapa.pk)
        self.assertEqual(generado.datos_utilizados['fecha_vencimiento'], self.etapa.fecha_vencimiento.isoformat())
        self.assertEqual(generado.datos_utilizados['plazo_dias_habiles'], 15)
        self.assertIn('sha256_original', generado.datos_utilizados['plantilla_origen'])
        self.assertIn('observaciones_pagos', generado.campos_pendientes)
        self.assertIn('consideracion_oportunidad', generado.campos_pendientes)
        self.assertIn('documentos_solicitados', generado.campos_pendientes)
        with generado.documento.archivo.open('rb') as archivo, ZipFile(archivo) as z:
            xml = z.read('word/document.xml').decode()
        if os.environ.get('SIGELC_MUESTRA'):
            with generado.documento.archivo.open('rb') as archivo:
                Path(os.environ['SIGELC_MUESTRA']).write_bytes(archivo.read())
        for dato in ['1441', 'MARITZA', 'PASTOS', 'Jayalaa', 'Christian Felipe', 'Dos  (2) facturas', '(01) de (05) de (2025)']:
            self.assertNotIn(dato, xml)
        self.assertIn('2025-12-31', xml)
        self.assertIn('[PENDIENTE: observaciones_pagos]', xml)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual_id, self.etapa.estado_id)
        self.assertFalse(self.contrato.historial_estados.exists())

    def test_lista_dinamica_y_paquete_conservado(self):
        self.preparar()
        for nombre in ('Documento FICTICIO A', 'Documento FICTICIO B'):
            tipo = TipoDocumental.objects.create(nombre=nombre, responsable=self.abogado)
            RequisitoDocumental.objects.create(tipo=tipo, clase_expediente=self.contrato.tipo_contrato, activo=True)
        generado = self.generar()
        with generado.documento.archivo.open('rb') as archivo:
            contenido = archivo.read()
        with ZipFile(BytesIO(self.original)) as original, ZipFile(BytesIO(contenido)) as nuevo:
            for parte in original.namelist():
                if parte != 'word/document.xml':
                    self.assertEqual(original.read(parte), nuevo.read(parte), parte)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            raiz = ET.fromstring(nuevo.read('word/document.xml'))
            for nombre in ('Documento FICTICIO A', 'Documento FICTICIO B'):
                p = next(p for p in raiz.findall('.//w:p',ns) if nombre in ''.join(p.itertext()))
                self.assertIsNotNone(p.find('w:pPr/w:numPr',ns))
            self.assertEqual(len(ET.fromstring(original.read('word/document.xml')).findall('.//w:tbl',ns)),len(raiz.findall('.//w:tbl',ns)))

    def test_etapa_inexistente_o_distinta_bloquea(self):
        self.preparar(iniciar=False)
        with self.assertRaisesMessage(ValidationError, 'instancia vigente'):
            self.generar()
        etapa = iniciar_seguimiento(contrato_id=self.contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True)
        destino = self.estado.transiciones_salientes.filter(activa=True).first().destino
        cambiar_estado(contrato_id=self.contrato.pk, estado_nuevo_id=destino.pk, instancia_esperada=etapa.pk,
                       usuario=self.abogado, motivo='Prueba ficticia', confirmado=True, fecha_efectiva=etapa.fecha_inicio)
        with self.assertRaisesMessage(ValidationError, 'instancia vigente'):
            self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())

    def test_previsualizacion_autorizada_reintento_y_descarga(self):
        self.preparar()
        self.client.force_login(self.abogado)
        url = reverse('admin:gestion_contractual_requerimientos', args=[self.contrato.pk])
        campos = preparar_primer_requerimiento(usuario=self.abogado, contrato_id=self.contrato.pk)
        self.assertIn('fecha_vencimiento', [c[0] for c in campos])
        self.assertContains(self.client.post(url, {'tipo':test_requerimientos.TIPO,'previsualizar':'1'}), 'InstanciaSeguimiento')
        self.assertFalse(DocumentoGenerado.objects.exists())
        op = uuid4()
        a = self.generar(operacion=op)
        self.assertEqual(self.generar(operacion=op).pk, a.pk)
        self.assertEqual(DocumentoGenerado.objects.count(),1)
        self.assertTrue(Auditoria.objects.filter(accion='generar_requerimiento',identificador_registro=str(a.pk)).exists())
        enlace = reverse('descargar_documento',args=[a.documento_id])
        respuesta = self.client.get(enlace)
        self.assertEqual(respuesta.status_code,200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:
            self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'PK'))
        finally:
            signals.request_finished.connect(close_old_connections)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(enlace).status_code,403)

    def test_reversion_y_revision_conservan_origen(self):
        self.preparar()
        archivos = {p for p in self.media.rglob('*') if p.is_file()}
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):
                self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual(archivos,{p for p in self.media.rglob('*') if p.is_file()})
        primero = self.generar()
        revision = self.generar(archivo_revision=test_requerimientos.docx(),anterior_id=primero.pk)
        self.assertEqual(revision.plantilla,self.plantilla)
        self.assertEqual(revision.datos_utilizados,primero.datos_utilizados)
        self.assertEqual(revision.anterior,primero)
        with self.plantilla.archivo.open('rb') as archivo:
            self.assertEqual(archivo.read(),self.original)

    def test_calendario_inconsistente_no_emite_documento(self):
        self.preparar()
        with patch('gestion_contractual.primer_requerimiento.calcular_vencimiento',return_value=(date(2026,1,27),{})):
            with self.assertRaisesMessage(ValidationError,'difiere del calendario'):
                self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertFalse(self.contrato.historial_estados.exists())
