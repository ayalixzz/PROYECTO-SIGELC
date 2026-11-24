from io import BytesIO
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as E
from zipfile import ZipFile

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.actas_observaciones import ROTULO, preparar_acta_observaciones
from gestion_contractual.models import Auditoria, DocumentoGenerado, ObservacionDocumental, RequisitoDocumental, SoporteObservacion, TipoDocumental
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.revision import recibir_documento, registrar_observacion, revisar_documento
from . import test_requerimientos

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class ObservacionesInstitucionalTest(TestCase):
    setUpTestData = classmethod(test_requerimientos.RequerimientosTest.setUpTestData.__func__)

    def setUp(self):
        test_requerimientos.RequerimientosTest.setUp(self)
        self.abogado.first_name, self.abogado.last_name = 'Abogada', 'FICTICIA'
        self.abogado.save()
        self.contrato.numero_contrato = 'FICTICIO-OBS-001'
        self.contrato.supervisor_nombre = 'Supervisora FICTICIA'
        self.contrato.objeto_contractual = 'Objeto FICTICIO para verificar el acta de observaciones.'
        self.contrato.save()
        self.path = Path(settings.BASE_DIR).parent/'plantillas_institucionales/observaciones/v1/acta-observaciones-institucional.docx'
        self.plantilla = self.registrar()

    def registrar(self):
        return registrar_plantilla(usuario=self.abogado, tipo='observaciones', nombre='Acta institucional — prueba FICTICIA',
            archivo=SimpleUploadedFile(self.path.name, self.path.read_bytes()), activar=True)

    def generar(self, **kwargs):
        return generar_requerimiento(usuario=kwargs.pop('usuario', self.abogado), contrato_id=self.contrato.pk,
                                      tipo='observaciones', **kwargs)

    def observar(self, **kwargs):
        datos = dict(descripcion='Aclaración FICTICIA sobre el soporte recibido.', tipo='aclaracion', estado_revision='observado',
                     evidencia='Referencia FICTICIA: página 1 del soporte.', documentos=[], operacion=uuid4())
        datos.update(kwargs)
        return registrar_observacion(usuario=self.abogado, contrato_id=self.contrato.pk, **datos)

    def soporte(self):
        tipo = TipoDocumental.objects.create(nombre='Informe FICTICIO', responsable=self.abogado)
        RequisitoDocumental.objects.create(tipo=tipo, clase_expediente='Prueba', activo=True)
        doc = recibir_documento(usuario=self.abogado, contrato_id=self.contrato.pk, categoria=tipo,
            archivo=SimpleUploadedFile('soporte-FICTICIO.pdf', b'%PDF-1.7'), fecha_recepcion=None,
            descripcion='FICTICIO', operacion=uuid4())
        revisar_documento(usuario=self.abogado, contrato_id=self.contrato.pk, documento=doc, estado='observado', estado_esperado='pendiente')
        return doc

    def contenido(self, generado):
        with generado.documento.archivo.open('rb') as f: return f.read()

    def texto(self, contenido):
        with ZipFile(BytesIO(contenido)) as z: root=E.fromstring(z.read('word/document.xml'))
        return '\n'.join(''.join(p.itertext()) for p in root.findall('.//'+W+'p'))

    def test_campos_instantanea_y_muestra(self):
        doc = self.soporte()
        anterior = self.observar(documentos=[doc], descripcion='Texto sustituido FICTICIO')
        vigente = self.observar(documentos=[doc], anterior=anterior, motivo='Precisar referencia FICTICIA')
        self.observar(tipo='faltante', descripcion='Solicitar anexo FICTICIO', documentos=[doc])
        generado = self.generar(operacion=uuid4())
        datos = generado.datos_utilizados
        self.assertEqual(datos['numero_contrato'], self.contrato.numero_contrato)
        self.assertEqual(datos['vigencia'], 2026)
        self.assertEqual(datos['abogado'], 'Abogada FICTICIA')
        snap = datos['expediente_observaciones']
        self.assertEqual(len(snap['observaciones']), 2)
        obs = snap['observaciones'][0]
        self.assertEqual(obs['id'], vigente.pk)
        self.assertEqual([h['id'] for h in obs['historial_rectificaciones']], [anterior.pk, vigente.pk])
        self.assertEqual(obs['documentos'][0]['id'], doc.pk)
        self.assertEqual(obs['responsable_id'], self.abogado.pk)
        self.assertTrue(obs['fecha_registro'])
        self.assertIn('fecha_revision', generado.campos_pendientes)
        self.assertIn('memorando_radicado', generado.campos_pendientes)
        contenido = self.contenido(generado)
        texto = self.texto(contenido)
        for esperado in [ROTULO, self.contrato.objeto_contractual, self.contrato.numero_contrato,
                         'Supervisora FICTICIA', vigente.descripcion, doc.nombre_original, '[PENDIENTE: plazo_ajustes]']:
            self.assertIn(esperado, texto)
        for ajeno in ['Jairo', '1249', 'Texto sustituido FICTICIO', '15 días hábiles']:
            self.assertNotIn(ajeno, texto)
        self.assertIn('Texto sustituido FICTICIO', str(obs['historial_rectificaciones']))
        if os.environ.get('SIGELC_MUESTRA_OBSERVACIONES'):
            Path(os.environ['SIGELC_MUESTRA_OBSERVACIONES']).write_bytes(contenido)

    def test_faltantes_sin_inferencias_y_sin_catalogo(self):
        generado = self.generar()
        self.assertIn('configuración institucional', self.texto(self.contenido(generado)))
        self.assertIn('observaciones_documentales', generado.campos_pendientes)
        tipo = TipoDocumental.objects.create(nombre='Anexo FICTICIO', responsable=self.abogado)
        RequisitoDocumental.objects.create(tipo=tipo, clase_expediente='Prueba', activo=True)
        self.observar(tipo='faltante', descripcion='Afirmación FICTICIA sin evidencia', evidencia='', estado_revision='pendiente')
        nuevo = self.generar()
        self.assertIn('requiere confirmación', str(nuevo.datos_utilizados['requisitos_faltantes']))
        self.assertNotIn('Afirmación FICTICIA sin evidencia', str(nuevo.datos_utilizados['faltantes_confirmados']))
        self.assertIn('PENDIENTE DE VALIDACIÓN O SOPORTE', self.texto(self.contenido(nuevo)))
        self.assertIn('validacion_soportes_observaciones', nuevo.campos_pendientes)

    def test_no_incluye_datos_ajenos_y_rechaza_relacion_invalida(self):
        otro = type(self.contrato).objects.get(pk=self.contrato.pk)
        otro.pk = None
        otro.numero_contrato = 'AJENO-NO-INCLUIR'
        otro.save()
        ajena = registrar_observacion(usuario=self.abogado, contrato_id=otro.pk, descripcion='HALLAZGO SECRETO AJENO',
            tipo='otro', estado_revision='observado', evidencia='EVIDENCIA AJENA', documentos=[], operacion=uuid4())
        ajeno = otro.documentos.create(tipo_documento='FICTICIO', nombre_original='AJENO.pdf',
            archivo=SimpleUploadedFile('AJENO.pdf', b'%PDF-1.7'), cargado_por=self.abogado)
        propia = self.observar()
        generado = self.generar()
        self.assertNotIn('AJENO', self.texto(self.contenido(generado)))
        self.assertNotIn(ajena.pk, [o['id'] for o in generado.datos_utilizados['expediente_observaciones']['observaciones']])
        # Corrupción simulada exclusivamente dentro del rollback de TestCase.
        models.QuerySet.bulk_create(SoporteObservacion.objects.all(), [SoporteObservacion(observacion=propia, documento=ajeno)])
        with self.assertRaisesMessage(ValidationError, 'otro contrato'): self.generar()

    def test_diseno_conserva_paquete_tabla_y_margenes(self):
        generado = self.generar()
        with ZipFile(self.path) as base, ZipFile(BytesIO(self.contenido(generado))) as salida:
            for nombre in base.namelist():
                if nombre != 'word/document.xml': self.assertEqual(base.read(nombre), salida.read(nombre), nombre)
            a,b = [E.fromstring(z.read('word/document.xml')) for z in (base,salida)]
        for tag in ['sectPr', 'tblPr', 'tblGrid']:
            self.assertEqual(E.tostring(a.find('.//'+W+tag)), E.tostring(b.find('.//'+W+tag)))
        self.assertEqual(len(a.findall('.//'+W+'tr')), len(b.findall('.//'+W+'tr')))
        self.assertEqual(len(b.findall('.//'+W+'tr')), 14)
        self.assertNotIn('pageBreakBefore', E.tostring(b).decode())

    def test_versionado_auditoria_reintentos_y_edicion(self):
        operacion = uuid4()
        uno = self.generar(operacion=operacion)
        contenido = self.contenido(uno)
        self.assertEqual(self.generar(operacion=operacion).pk, uno.pk)
        self.registrar()
        dos = self.generar(archivo_revision=SimpleUploadedFile('editado.docx', contenido), anterior_id=uno.pk, motivo='Revisión FICTICIA')
        self.assertEqual(dos.version, 2)
        self.assertEqual(dos.plantilla_id, uno.plantilla_id)
        self.assertEqual(dos.anterior_id, uno.pk)
        self.assertIn('revision_manual', dos.datos_utilizados)
        self.assertEqual(dos.datos_utilizados['expediente_observaciones'], uno.datos_utilizados['expediente_observaciones'])
        tres = self.generar()
        self.assertEqual(tres.plantilla.version, 2)
        self.assertEqual(tres.version, 3)
        self.assertEqual(self.contenido(uno), contenido)
        self.assertEqual(Auditoria.objects.filter(entidad_afectada='DocumentoGenerado').count(), 3)
        with self.assertRaises(ValidationError):
            self.generar(archivo_revision=SimpleUploadedFile('viejo.docx', contenido), anterior_id=uno.pk, motivo='Obsoleto')

    def test_revision_exige_motivo_y_rotulo(self):
        uno = self.generar()
        with self.assertRaisesMessage(ValidationError, 'motivo'):
            self.generar(archivo_revision=SimpleUploadedFile('editado.docx', self.contenido(uno)), anterior_id=uno.pk)
        with self.assertRaisesMessage(ValidationError, 'rótulo'):
            self.generar(archivo_revision=test_requerimientos.docx(), anterior_id=uno.pk, motivo='Prueba')
        self.assertEqual(DocumentoGenerado.objects.count(), 1)

    def test_admin_previsualiza_y_genera_sin_cambiar_estado(self):
        self.soporte()
        self.observar()
        self.client.force_login(self.abogado)
        ficha=reverse('admin:gestion_contractual_contrato_change',args=[self.contrato.pk])
        self.assertContains(self.client.get(ficha), 'GENERAR ACTA DE OBSERVACIONES')
        url=reverse('admin:gestion_contractual_requerimientos', args=[self.contrato.pk])
        cantidad=DocumentoGenerado.objects.count()
        preview=self.client.post(url, {'tipo':'observaciones','previsualizar':'1'})
        self.assertContains(preview,'Historial de rectificaciones')
        self.assertContains(preview,'soporte-FICTICIO.pdf')
        self.assertEqual(DocumentoGenerado.objects.count(),cantidad)
        self.assertEqual(self.client.post(url,{'tipo':'observaciones','operacion':str(uuid4())}).status_code,302)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual_id, self.estado.pk)
        self.assertFalse(self.contrato.historial_estados.exists())
        self.assertFalse(self.contrato.instancias.exists())

    def test_permisos_y_descarga_privada(self):
        doc=self.generar().documento
        url=reverse('descargar_documento',args=[doc.pk])
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code,403)
        with self.assertRaises(PermissionDenied): self.generar(usuario=self.otro)
        with self.assertRaises(PermissionDenied): preparar_acta_observaciones(usuario=self.otro,contrato_id=self.contrato.pk)
        self.client.force_login(self.abogado)
        respuesta=self.client.get(url)
        self.assertEqual(respuesta.status_code,200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try: self.assertTrue(b''.join(respuesta.streaming_content).startswith(b'PK'))
        finally: signals.request_finished.connect(close_old_connections)

    def test_rollback_no_deja_archivos_ni_auditoria(self):
        inicial={p for p in self.media.rglob('*') if p.is_file()}
        cantidad=Auditoria.objects.count()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError): self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual(Auditoria.objects.count(),cantidad)
        self.assertEqual({p for p in self.media.rglob('*') if p.is_file()},inicial)
        with self.assertRaises(ValidationError):
            self.generar(archivo_revision=test_requerimientos.docx('<w:r><w:t>inválido</w:t></w:r>'))

    def test_abogado_sin_permiso_de_generacion(self):
        from django.contrib.auth.models import Permission
        permiso=Permission.objects.get(content_type__app_label='gestion_contractual', codename='add_documentocontrato')
        self.abogado.user_permissions.remove(permiso)
        for atributo in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.abogado.__dict__.pop(atributo, None)
        with self.assertRaises(PermissionDenied): self.generar()
        with self.assertRaises(PermissionDenied): preparar_acta_observaciones(usuario=self.abogado,contrato_id=self.contrato.pk)

    def test_instantanea_inmutable_tras_rectificar(self):
        anterior=self.observar()
        uno=self.generar()
        self.observar(anterior=anterior, motivo='Rectificación FICTICIA posterior',descripcion='Nueva descripción FICTICIA')
        dos=self.generar()
        uno.refresh_from_db()
        self.assertEqual(uno.datos_utilizados['expediente_observaciones']['observaciones'][0]['id'],anterior.pk)
        self.assertNotEqual(uno.datos_utilizados['expediente_observaciones'],dos.datos_utilizados['expediente_observaciones'])
        with self.assertRaises(ValidationError): ObservacionDocumental.objects.filter(pk=anterior.pk).update(descripcion='alterado')
