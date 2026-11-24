from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from . import test_requerimientos
from gestion_contractual.models import Auditoria, DocumentoContrato, EstadoRevision, ObservacionDocumental, RequisitoDocumental, TipoDocumental
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.revision import SIN_REQUISITOS, recibir_documento, registrar_observacion, resumen_documental, revisar_documento


class RevisionTest(TestCase):
    setUpTestData = classmethod(test_requerimientos.RequerimientosTest.setUpTestData.__func__)
    setUp = test_requerimientos.RequerimientosTest.setUp

    def tipo(self):
        return TipoDocumental.objects.create(nombre='Soporte ficticio', responsable=self.abogado)

    def recibir(self, categoria, **otros):
        datos = {'categoria': categoria, 'archivo': SimpleUploadedFile('ficticio.pdf', b'%PDF-1.7'),
                 'fecha_recepcion': None, 'descripcion': '', 'operacion': uuid4()}
        return recibir_documento(usuario=self.abogado, contrato_id=self.contrato.pk, **(datos | otros))

    def observar(self, **otros):
        datos = {'descripcion': 'Hallazgo ficticio', 'tipo': 'aclaracion', 'estado_revision': 'observado',
                 'evidencia': 'Referencia ficticia', 'documentos': [], 'operacion': uuid4()}
        return registrar_observacion(usuario=self.abogado, contrato_id=self.contrato.pk, **(datos | otros))

    def test_catalogo_aplicabilidad_y_faltantes(self):
        self.client.force_login(self.abogado)
        alta = reverse('admin:gestion_contractual_tipodocumental_add')
        self.assertEqual(self.client.post(alta, {'nombre': 'Soporte ficticio', 'activo': 'on'}).status_code, 302)
        tipo = TipoDocumental.objects.get()
        self.assertEqual(tipo.responsable, self.abogado)
        RequisitoDocumental.objects.create(tipo=tipo, activo=True)
        RequisitoDocumental.objects.create(tipo=tipo, clase_expediente='Otra clase', activo=True)
        self.assertEqual(resumen_documental(self.contrato)['aviso'], SIN_REQUISITOS)
        RequisitoDocumental.objects.create(tipo=tipo, clase_expediente='prueba', activo=True)

        def estado():
            return resumen_documental(self.contrato)['requisitos'][0]['estado']

        self.assertEqual(estado(), 'Faltante')
        documento = self.recibir(tipo)
        self.assertEqual(estado(), 'Pendiente de revisión')
        revisar_documento(usuario=self.abogado, contrato_id=self.contrato.pk, documento=documento,
                         estado='no_aplica', estado_esperado='pendiente')
        self.assertEqual(estado(), 'No aplica')
        revisar_documento(usuario=self.abogado, contrato_id=self.contrato.pk, documento=documento,
                         estado='observado', estado_esperado='no_aplica')
        self.assertEqual(estado(), 'Aportado')

    def test_versiones_rectificacion_reintento_y_descarga(self):
        categoria = self.tipo()
        clave = uuid4()
        primera = self.recibir(categoria, operacion=clave)
        self.assertEqual(self.recibir(categoria, operacion=clave).pk, primera.pk)
        observacion = self.observar(documentos=[primera])
        segunda = self.recibir(categoria, anterior=primera, motivo='Rectificación FICTICIA de soporte', archivo=SimpleUploadedFile('nuevo.pdf', b'%PDF-1.7 nuevo'))
        self.assertEqual(segunda.version, 2)
        with primera.archivo.open('rb') as archivo:
            self.assertEqual(archivo.read(), b'%PDF-1.7')
        primera.archivo = 'archivo-sustituido.pdf'
        with self.assertRaises(ValidationError):
            primera.save()
        with self.assertRaises(ValidationError):
            self.observar(anterior=observacion)
        operacion = uuid4()
        correccion = self.observar(anterior=observacion, documentos=[segunda], descripcion='Rectificación ficticia',
                                   motivo='Referencia corregida', operacion=operacion)
        self.assertEqual(self.observar(operacion=operacion).pk, correccion.pk)
        self.assertEqual(observacion.documentos.get().pk, primera.pk)
        self.assertEqual(correccion.anterior.descripcion, 'Hallazgo ficticio')
        with self.assertRaises(ValidationError):
            observacion.delete()
        self.client.force_login(self.abogado)
        self.assertContains(self.client.get(reverse('admin:gestion_contractual_revision_documental', args=[self.contrato.pk])), 'Rectificación ficticia')
        url = reverse('descargar_documento', args=[primera.pk])
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:
            self.assertEqual(b''.join(respuesta.streaming_content), b'%PDF-1.7')
        finally:
            signals.request_finished.connect(close_old_connections)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            registrar_observacion(usuario=self.otro, contrato_id=self.contrato.pk, descripcion='x', tipo='otro',
                                  estado_revision='pendiente', evidencia='', documentos=[], operacion=uuid4())

    def test_soportes_ajenos_y_reversion(self):
        categoria = self.tipo()
        otro = type(self.contrato).objects.get(pk=self.contrato.pk)
        otro.pk = None
        otro.numero_contrato = 'TEST-OTRO'
        otro.save()
        ajeno = DocumentoContrato.objects.create(
            contrato=otro, tipo_documento='Prueba', categoria=categoria, nombre_original='ajeno.pdf',
            archivo=SimpleUploadedFile('ajeno.pdf', b'%PDF-1.7'), cargado_por=self.abogado,
        )
        with self.assertRaises(ValidationError):
            self.observar(documentos=[ajeno])
        with self.assertRaises(ValidationError):
            self.recibir(categoria, anterior=ajeno)
        archivos = {p for p in self.media.rglob('*') if p.is_file()}
        with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('fallo simulado')):
            with self.assertRaises(RuntimeError):
                self.observar()
            with self.assertRaises(RuntimeError):
                self.recibir(categoria)
        self.assertFalse(ObservacionDocumental.objects.exists())
        self.assertFalse(self.contrato.documentos.exists())
        self.assertEqual({p for p in self.media.rglob('*') if p.is_file()}, archivos)

    def test_borrador_usa_resumen_y_conserva_estado(self):
        datos = {'usuario': self.abogado, 'contrato_id': self.contrato.pk, 'tipo': 'observaciones'}
        with self.assertRaises(ValidationError):
            generar_requerimiento(**datos)
        self.observar(descripcion='Línea uno\nLínea dos')
        registrar_plantilla(usuario=self.abogado, tipo='observaciones', nombre='FICTICIA',
                           archivo=test_requerimientos.docx('<w:r><w:t>{{requisitos_faltantes}} {{observaciones_documentales}}</w:t></w:r>'), activar=True)
        clave = uuid4()
        borrador = generar_requerimiento(**datos, operacion=clave)
        self.assertEqual(generar_requerimiento(**datos, operacion=clave).pk, borrador.pk)
        with borrador.documento.archivo.open('rb') as archivo, ZipFile(archivo) as paquete:
            xml = paquete.read('word/document.xml').decode()
        self.assertIn(SIN_REQUISITOS, xml)
        self.assertIn('Línea uno</w:t><w:br/>', xml)
        self.assertEqual(len(borrador.datos_utilizados['resumen_documental']['observaciones']), 1)
        revision = generar_requerimiento(**datos, anterior_id=borrador.pk, archivo_revision=test_requerimientos.docx())
        self.assertEqual(revision.anterior, borrador)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual, self.estado)
        self.assertFalse(self.contrato.historial_estados.exists())
