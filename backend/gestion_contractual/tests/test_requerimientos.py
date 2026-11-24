from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gestion_contractual.models import Auditoria, Contrato, DocumentoGenerado, EntidadPublica, EstadoContrato, PerfilUsuario, PlantillaDocumento
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla


TIPO = 'primer-requerimiento'


def docx(texto='<w:r><w:t>{{numero_</w:t></w:r><w:r><w:t>contrato}} {{fecha_inicio}}</w:t></w:r>'):
    archivo = BytesIO()
    with ZipFile(archivo, 'w') as paquete:
        paquete.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        paquete.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        paquete.writestr('word/document.xml', f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>{texto}</w:p></w:body></w:document>')
    return SimpleUploadedFile('FICTICIA.docx', archivo.getvalue())


class RequerimientosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.abogado = User.objects.create_user('abogado-ficticio', is_staff=True)
        cls.otro = User.objects.create_user('otro-ficticio', is_staff=True)
        permisos = Permission.objects.filter(content_type__app_label='gestion_contractual')
        cls.abogado.user_permissions.set(permisos)
        cls.otro.user_permissions.set(permisos)
        PerfilUsuario.objects.create(usuario=cls.abogado, abogado_operativo=True)
        cls.estado = EstadoContrato.objects.get(codigo=TIPO)
        cls.contrato = Contrato.objects.create(
            entidad=EntidadPublica.objects.create(nombre='Entidad ficticia', nit='TEST-A'),
            numero_contrato='TEST-A-001', anio_contractual=2026, objeto_contractual='Objeto ficticio',
            tipo_contrato='Prueba', modalidad_contratacion='Prueba', contratista_nombre='Contratista ficticio',
            contratista_identificacion='TEST', valor_inicial=100, valor_actual=100,
            fecha_suscripcion=date(2026, 1, 1), estado_actual=cls.estado, abogado_responsable=cls.abogado,
        )

    def setUp(self):
        carpeta = TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.media = Path(carpeta.name)
        ajuste = override_settings(MEDIA_ROOT=carpeta.name)
        ajuste.enable()
        self.addCleanup(ajuste.disable)

    def registrar(self, archivo=None, tipo=TIPO):
        return registrar_plantilla(usuario=self.abogado, tipo=tipo, nombre='Plantilla FICTICIA',
                                   archivo=docx() if archivo is None else archivo, activar=True)

    def generar(self, **kwargs):
        return generar_requerimiento(usuario=kwargs.pop('usuario', self.abogado),
                                     contrato_id=self.contrato.pk, tipo=TIPO, **kwargs)

    def test_versionado_reemplazo_y_original_intacto(self):
        archivo = docx()
        original = archivo.read()
        primera = self.registrar(archivo)
        borrador = self.generar()
        with borrador.documento.archivo.open('rb') as archivo, ZipFile(archivo) as paquete:
            xml = paquete.read('word/document.xml')
        self.assertIn(b'TEST-A-001', xml)
        self.assertIn(b'[PENDIENTE: fecha_inicio]', xml)
        self.assertEqual(borrador.campos_pendientes, ['fecha_inicio'])
        self.assertEqual(borrador.contrato_id, self.contrato.pk)
        segunda = self.registrar()
        revision = self.generar(archivo_revision=docx('<w:r><w:t>Texto editado</w:t></w:r>'), anterior_id=borrador.pk)
        self.assertEqual((primera.version, segunda.version, revision.version), (1, 2, 2))
        self.assertEqual(revision.plantilla, primera)
        self.assertEqual(revision.anterior, borrador)
        self.assertNotEqual(revision.documento.archivo.name, borrador.documento.archivo.name)
        primera.refresh_from_db()
        self.assertFalse(primera.activa)
        with primera.archivo.open('rb') as archivo:
            self.assertEqual(archivo.read(), original)
        self.assertEqual(self.generar().plantilla, segunda)
        with self.assertRaises(ValidationError):
            self.generar(archivo_revision=docx(), anterior_id=borrador.pk)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual, self.estado)
        self.assertFalse(self.contrato.historial_estados.exists())
        self.assertEqual(Auditoria.objects.filter(accion='generar_requerimiento').count(), 2)
        self.assertTrue(Auditoria.objects.filter(accion='revisar_requerimiento').exists())

    def test_rechazos_y_reversion(self):
        with self.assertRaises(ValidationError):
            self.generar()
        for archivo in (SimpleUploadedFile('mal.docx', b'ilegible'), SimpleUploadedFile('plantilla.pdf', b'pdf')):
            with self.assertRaises(ValidationError):
                self.registrar(archivo)
        with self.assertRaises(ValidationError):
            self.registrar(tipo='acta')
        self.assertFalse(PlantillaDocumento.objects.exists())
        self.registrar()
        archivos = {p for p in self.media.rglob('*') if p.is_file()}
        with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('fallo simulado')):
            with self.assertRaises(RuntimeError):
                self.generar()
            with self.assertRaises(RuntimeError):
                self.registrar()
        self.assertEqual(PlantillaDocumento.objects.filter(activa=True).get().version, 1)
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertFalse(self.contrato.documentos.exists())
        self.assertEqual({p for p in self.media.rglob('*') if p.is_file()}, archivos)

    def test_flujo_admin_y_descarga_privada(self):
        plantillas = reverse('admin:gestion_contractual_plantillas_requerimiento')
        url = reverse('admin:gestion_contractual_requerimientos', args=[self.contrato.pk])
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.generar(usuario=self.otro)
        self.client.force_login(self.abogado)
        self.assertEqual(self.client.post(plantillas, {'tipo': TIPO, 'nombre': 'Ficticia', 'archivo': docx(), 'activar': 'on'}).status_code, 302)
        pk = PlantillaDocumento.objects.get().pk
        self.assertEqual(self.client.post(plantillas, {'plantilla_id': pk, 'activar_version': 'no'}).status_code, 302)
        self.assertFalse(PlantillaDocumento.objects.get(pk=pk).activa)
        self.assertEqual(self.client.post(plantillas, {'plantilla_id': pk, 'activar_version': 'si'}).status_code, 302)
        self.assertTrue(PlantillaDocumento.objects.get(pk=pk).activa)
        self.assertEqual(self.client.post(url, {'tipo': TIPO}).status_code, 302)
        self.assertContains(self.client.get(url), 'Campos pendientes')
        borrador = DocumentoGenerado.objects.get()
        enlace = reverse('descargar_documento', args=[borrador.documento_id])
        respuesta = self.client.get(enlace)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('attachment', respuesta.headers['Content-Disposition'])
        # El cliente de pruebas cierra la respuesta al consumir el flujo.
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:
            self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'PK'))
        finally:
            signals.request_finished.connect(close_old_connections)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(enlace).status_code, 403)
