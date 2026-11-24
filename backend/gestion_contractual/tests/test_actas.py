from datetime import date
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.competencia import registrar_calculo
from gestion_contractual.models import Auditoria, DocumentoGenerado, TipoActa
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from . import test_requerimientos


class ActasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        from .calendarios_ficticios import aprobar_regresion
        aprobar_regresion(cls.abogado)
    setUp = test_requerimientos.RequerimientosTest.setUp

    def plantilla(self, tipo):
        archivo = test_requerimientos.docx('<w:r><w:t>{{numero_contrato}} {{pagos_realizados}} {{valor_contrato}} {{fecha_limite_competencia}}</w:t></w:r>')
        contenido = archivo.read()
        plantilla = registrar_plantilla(usuario=self.abogado, tipo=tipo.clave_documental, tipo_acta=tipo,
                                       nombre='Ficticia', archivo=archivo, activar=True, ficticia=True)
        return plantilla, contenido

    def generar(self, plantilla, **opciones):
        datos = dict(usuario=self.abogado, contrato_id=self.contrato.pk, tipo='', tipo_acta_id=plantilla.tipo_acta_id,
                     plantilla_id=plantilla.pk, operacion=uuid4())
        return generar_requerimiento(**(datos | opciones))

    def xml(self, borrador):
        with borrador.documento.archivo.open('rb') as archivo, ZipFile(archivo) as paquete:
            return paquete.read('word/document.xml').decode()

    def test_admin_catalogo_vista_previa_generacion_y_descarga(self):
        self.client.force_login(self.abogado)
        self.assertEqual(self.client.post(reverse('admin:gestion_contractual_tipoacta_add'), {'nombre': 'Acta ficticia', 'activo': 'on'}).status_code, 302)
        tipo = TipoActa.objects.get()
        plantilla, original = self.plantilla(tipo)
        url = reverse('admin:gestion_contractual_requerimientos', args=[self.contrato.pk])
        datos = {'tipo_acta': tipo.pk, 'plantilla': plantilla.pk, 'operacion': uuid4()}
        self.assertContains(self.client.post(url, {**datos, 'previsualizar': '1'}), 'pagos_realizados')
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        borrador = DocumentoGenerado.objects.get()
        xml = self.xml(borrador)
        self.assertIn('TEST-A-001', xml)
        self.assertIn('BORRADOR — PENDIENTE DE REVISIÓN JURÍDICA', xml)
        self.assertIn('DOCUMENTO FICTICIO NO INSTITUCIONAL', xml)
        self.assertIn('[PENDIENTE: pagos_realizados]', xml)
        self.assertIn('[PENDIENTE: valor_contrato]', xml)
        self.assertIn('[PENDIENTE: fecha_limite_competencia]', xml)
        with plantilla.archivo.open('rb') as archivo:
            self.assertEqual(archivo.read(), original)
        enlace = reverse('descargar_documento', args=[borrador.documento_id])
        respuesta = self.client.get(enlace)
        self.assertEqual(respuesta.status_code, 200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:
            self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'PK'))
        finally:
            signals.request_finished.connect(close_old_connections)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(enlace).status_code, 403)

    def test_fechas_vigentes_y_revision_conservada(self):
        plantilla, _ = self.plantilla(TipoActa.objects.create(nombre='Acta ficticia'))
        self.contrato.fecha_terminacion = date(2025, 2, 12)
        self.contrato.save()
        args = dict(contrato_id=self.contrato.pk, usuario=self.abogado)
        primero = registrar_calculo(**args, operacion=uuid4(), anterior_id=None, datos={
            'sujeto': 'si', 'regimen': 'art11_cpaca', 'origen': 'ejecucion', 'pacto': 'no',
            'liquidacion': 'ninguna', 'incidencias': 'no',
        })
        self.assertIsNone(self.generar(plantilla).calculo_competencia)
        verificado = registrar_calculo(**args, operacion=uuid4(), anterior_id=primero.pk, accion='verificar', fundamento='Prueba de fundamento')
        borrador = self.generar(plantilla)
        self.assertEqual(borrador.calculo_competencia, verificado)
        self.assertIn('2027-08-12', self.xml(borrador))
        self.contrato.fecha_terminacion = date(2025, 2, 13)
        self.contrato.save()
        with self.assertRaises(ValidationError):
            self.generar(plantilla, archivo_revision=test_requerimientos.docx(), anterior_id=borrador.pk, motivo='Corrección ficticia')
        actualizado = self.generar(plantilla)
        revision = self.generar(plantilla, archivo_revision=test_requerimientos.docx(), anterior_id=actualizado.pk, motivo='Corrección ficticia')
        self.assertEqual(revision.anterior, actualizado)
        self.assertIsNone(revision.calculo_competencia)
        self.assertIn('BORRADOR', self.xml(revision))
        self.assertIn('2027-08-12', self.xml(borrador))
        self.assertIsNone(self.generar(plantilla).calculo_competencia)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual, self.estado)
        self.assertFalse(self.contrato.historial_estados.exists())

    def test_permisos_compatibilidad_y_reversion(self):
        plantilla, _ = self.plantilla(TipoActa.objects.create(nombre='Acta ficticia'))
        otro_tipo = TipoActa.objects.create(nombre='Otro tipo')
        with self.assertRaises(PermissionDenied):
            self.generar(plantilla, usuario=self.otro)
        with self.assertRaises(ValidationError):
            self.generar(plantilla, tipo_acta_id=otro_tipo.pk)
        con = type(self.contrato).objects.get(pk=self.contrato.pk)
        con.pk = None
        con.numero_contrato = 'OTRO'
        con.save()
        borrador = self.generar(plantilla)
        with self.assertRaises(ValidationError):
            self.generar(plantilla, contrato_id=con.pk, archivo_revision=test_requerimientos.docx(), anterior_id=borrador.pk, motivo='Ajeno')
        archivos = {p for p in self.media.rglob('*') if p.is_file()}
        with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('Fallo simulado')):
            with self.assertRaises(RuntimeError):
                self.generar(plantilla)
        self.assertEqual(DocumentoGenerado.objects.count(), 1)
        self.assertEqual({p for p in self.media.rglob('*') if p.is_file()}, archivos)
