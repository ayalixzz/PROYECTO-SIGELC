from datetime import date, timedelta
from io import BytesIO
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from gestion_contractual.models import Auditoria, DocumentoGenerado, RequisitoDocumental, TipoDocumental
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.requerimientos_sucesivos import preparar_requerimiento_sucesivo
from gestion_contractual.revision import recibir_documento, revisar_documento
from gestion_contractual.services import cambiar_estado, iniciar_seguimiento
from . import test_requerimientos


TIPOS = [('segundo-requerimiento', 8, date(2026, 1, 15)), ('tercer-requerimiento', 5, date(2026, 1, 23))]
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class RequerimientosSucesivosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        from .calendarios_ficticios import aprobar_regresion
        aprobar_regresion(cls.abogado)

    def setUp(self):
        test_requerimientos.RequerimientosTest.setUp(self)
        self.abogado.first_name = 'Abogada'
        self.abogado.last_name = 'FICTICIA'
        self.abogado.save()
        self.contrato.entidad.nombre = 'ENTIDAD FICTICIA PARA PRUEBAS — SIN VALIDEZ'
        self.contrato.entidad.save()
        self.contrato.fecha_asignacion = date(2026, 1, 2)
        self.contrato.fecha_terminacion = date(2026, 1, 1)
        self.contrato.supervisor_nombre = 'Supervisor FICTICIO'
        self.contrato.objeto_contractual = 'Objeto FICTICIO de prueba documental.'
        self.contrato.save()
        self.plantillas = {}

    def registrar(self, tipo):
        prefix = '02-' if tipo.startswith('segundo') else '03-'
        path = next((Path(settings.BASE_DIR).parent/'plantillas_institucionales/v2/preparadas').glob(prefix+'*.docx'))
        return registrar_plantilla(usuario=self.abogado, tipo=tipo, nombre=f'Institucional {tipo} — copia v2',
                                   archivo=SimpleUploadedFile(path.name, path.read_bytes()), activar=True)

    def preparar(self, tipo, plantillas=True):
        if plantillas and not self.plantillas:
            self.plantillas = {codigo: self.registrar(codigo) for codigo, _, _ in TIPOS}
        if not self.contrato.instancias.exists():
            iniciar_seguimiento(contrato_id=self.contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True)
        self.contrato.refresh_from_db()
        while self.contrato.estado_actual.codigo != tipo:
            destino = self.contrato.estado_actual.transiciones_salientes.get(activa=True).destino
            etapa = self.contrato.instancias.get(fecha_finalizacion__isnull=True)
            fecha = date(2026, 1, 16) if destino.codigo in ('revision-abogado-2', 'revision-supervisor-2', 'tercer-requerimiento') else date(2026, 1, 2)
            cambiar_estado(contrato_id=self.contrato.pk, estado_nuevo_id=destino.pk, instancia_esperada=etapa.pk,
                           usuario=self.abogado, motivo='FICTICIO: preparar escenario de pruebas', confirmado=True,
                           fecha_efectiva=fecha)
            self.contrato.refresh_from_db()
        return self.contrato.instancias.get(fecha_finalizacion__isnull=True)

    def generar(self, tipo, **kwargs):
        return generar_requerimiento(usuario=kwargs.pop('usuario', self.abogado), contrato_id=self.contrato.pk, tipo=tipo, **kwargs)

    def contenido(self, generado):
        with generado.documento.archivo.open('rb') as archivo:
            return archivo.read()

    def textos(self, contenido):
        with ZipFile(BytesIO(contenido)) as z:
            root = ET.fromstring(z.read('word/document.xml'))
        return [''.join(n.itertext()) for n in root.findall('.//'+W+'p')]

    def test_generacion_por_etapa_plazos_datos_y_muestras(self):
        for nombre in ('Solicitud FICTICIA de liquidación', 'Informe final FICTICIO con soportes'):
            tipo = TipoDocumental.objects.create(nombre=nombre, responsable=self.abogado)
            RequisitoDocumental.objects.create(tipo=tipo, clase_expediente=self.contrato.tipo_contrato, activo=True)
        for tipo, dias, vence in TIPOS:
            with self.subTest(tipo=tipo):
                etapa = self.preparar(tipo)
                historial_antes = list(self.contrato.historial_estados.values_list('pk', flat=True))
                etapas_antes = list(self.contrato.instancias.values_list('pk', 'fecha_finalizacion'))
                with patch('gestion_contractual.requerimientos.timezone.localdate', return_value=etapa.fecha_inicio):
                    generado = self.generar(tipo)
                self.assertEqual(generado.plantilla, self.plantillas[tipo])
                self.assertEqual(etapa.fecha_vencimiento, vence)
                datos = generado.datos_utilizados
                self.assertEqual(datos['plazo_dias_habiles'], dias)
                self.assertEqual(datos['fecha_vencimiento'], vence.isoformat())
                self.assertEqual(datos['fecha_elaboracion'], etapa.fecha_inicio.isoformat())
                self.assertEqual(datos['numero_contrato'], self.contrato.numero_contrato)
                self.assertEqual(datos['vigencia'], 2026)
                self.assertEqual(datos['objeto'], self.contrato.objeto_contractual)
                self.assertEqual(datos['entidad'], self.contrato.entidad.nombre)
                self.assertEqual(datos['abogado'], 'Abogada FICTICIA')
                self.assertEqual(datos['actuacion_origen']['id'], etapa.pk)
                self.assertIn('sha256_original', datos['plantilla_origen'])
                self.assertEqual(datos['plantilla_origen']['version_trabajo'], 2)
                self.assertIn('dependencia', generado.campos_pendientes)
                self.assertIn('revisor', generado.campos_pendientes)
                self.assertIn('observaciones_pagos', generado.campos_pendientes)
                self.assertNotIn('documentos_solicitados', generado.campos_pendientes)
                contenido = self.contenido(generado)
                texto = '\n'.join(self.textos(contenido))
                for dato in ['1441', 'MARITZA', 'PASTOS', 'Christian Felipe', 'Jayalaa', 'Dos  (2) facturas', '(2025)']:
                    self.assertNotIn(dato, texto)
                self.assertIn(vence.isoformat(), texto)
                self.assertIn('Supervisor FICTICIO', texto)
                self.assertIn('[PENDIENTE: revisor]', texto)
                self.contrato.refresh_from_db()
                self.assertEqual(self.contrato.estado_actual.codigo, tipo)
                self.assertEqual(list(self.contrato.historial_estados.values_list('pk', flat=True)), historial_antes)
                self.assertEqual(list(self.contrato.instancias.values_list('pk', 'fecha_finalizacion')), etapas_antes)
                if os.environ.get('SIGELC_MUESTRAS_SUCESIVAS'):
                    (Path(os.environ['SIGELC_MUESTRAS_SUCESIVAS'])/f'PRUEBA-FICTICIA-{tipo}.docx').write_bytes(contenido)

    def test_rechaza_etapas_incompatibles_y_calendario_inconsistente(self):
        self.plantillas = {tipo: self.registrar(tipo) for tipo, _, _ in TIPOS}
        for tipo, _, _ in TIPOS:
            with self.assertRaisesMessage(ValidationError, 'instancia vigente'):
                self.generar(tipo)
        etapa = self.preparar('segundo-requerimiento')
        with self.assertRaisesMessage(ValidationError, 'instancia vigente'):
            self.generar('tercer-requerimiento')
        for tipo, _, _ in TIPOS:
            etapa = self.preparar(tipo)
            with patch('gestion_contractual.requerimientos_sucesivos.calcular_vencimiento', return_value=(etapa.fecha_vencimiento+timedelta(days=1), {})):
                with self.assertRaisesMessage(ValidationError, 'difiere del calendario'):
                    self.generar(tipo)
        self.assertFalse(DocumentoGenerado.objects.exists())

    def test_versionado_reintentos_auditoria_y_revisiones(self):
        # Una asignación histórica no atribuye falsamente la elaboración a otra persona.
        self.contrato.abogado_responsable = self.otro
        self.contrato.save()
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                operacion = uuid4()
                primero = self.generar(tipo, operacion=operacion)
                self.assertEqual(primero.datos_utilizados['abogado'], self.abogado.get_full_name())
                self.assertEqual(self.generar(tipo, operacion=operacion).pk, primero.pk)
                original = self.contenido(primero)
                segunda_plantilla = self.registrar(tipo)
                revision = self.generar(tipo, archivo_revision=test_requerimientos.docx(), anterior_id=primero.pk)
                self.assertEqual(revision.plantilla, primero.plantilla)
                self.assertEqual(revision.datos_utilizados, primero.datos_utilizados)
                self.assertEqual(revision.anterior, primero)
                self.assertEqual(revision.version, 2)
                tercero = self.generar(tipo)
                self.assertEqual(tercero.version, 3)
                self.assertEqual(tercero.plantilla, segunda_plantilla)
                self.assertEqual(original, self.contenido(primero))
                self.assertTrue(Auditoria.objects.filter(accion='generar_requerimiento', identificador_registro=str(primero.pk)).exists())
                self.assertTrue(Auditoria.objects.filter(accion='revisar_requerimiento', identificador_registro=str(revision.pk)).exists())
                with self.assertRaises(ValidationError):
                    self.generar(tipo, archivo_revision=test_requerimientos.docx(), anterior_id=primero.pk)

    def test_previsualizacion_y_generacion_en_admin(self):
        self.client.force_login(self.abogado)
        url = reverse('admin:gestion_contractual_requerimientos', args=[self.contrato.pk])
        for tipo, _, vence in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                page = self.client.get(url)
                self.assertEqual(page.context_data['form'].initial['tipo'], tipo)
                self.assertContains(page, str(self.plantillas[tipo]))
                cantidad = DocumentoGenerado.objects.count()
                campos = preparar_requerimiento_sucesivo(usuario=self.abogado, contrato_id=self.contrato.pk, tipo=tipo)
                self.assertIn(('fecha_vencimiento', vence.isoformat()), [(c, v) for c, v, _ in campos])
                preview = self.client.post(url, {'tipo': tipo, 'previsualizar': '1'})
                self.assertContains(preview, vence.isoformat())
                self.assertEqual(DocumentoGenerado.objects.count(), cantidad)
                self.assertEqual(self.client.post(url, {'tipo': tipo, 'operacion': uuid4()}).status_code, 302)
                self.assertEqual(DocumentoGenerado.objects.count(), cantidad+1)

    def test_descarga_privada_y_permisos(self):
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                with self.assertRaises(PermissionDenied):
                    self.generar(tipo, usuario=self.otro)
                generado = self.generar(tipo)
                enlace = reverse('descargar_documento', args=[generado.documento_id])
                self.client.logout()
                self.assertEqual(self.client.get(enlace).status_code, 302)
                self.client.force_login(self.otro)
                self.assertEqual(self.client.get(enlace).status_code, 403)
                self.client.force_login(self.abogado)
                respuesta = self.client.get(enlace)
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn('attachment', respuesta.headers['Content-Disposition'])
                from django.core import signals
                from django.db import close_old_connections
                signals.request_finished.disconnect(close_old_connections)
                try:
                    self.assertEqual(b''.join(respuesta.streaming_content), self.contenido(generado))
                finally:
                    signals.request_finished.connect(close_old_connections)
        permiso = Permission.objects.get(content_type__app_label='gestion_contractual', codename='add_documentocontrato')
        self.abogado.user_permissions.remove(permiso)
        restringido = get_user_model().objects.get(pk=self.abogado.pk)
        with self.assertRaises(PermissionDenied):
            self.generar('tercer-requerimiento', usuario=restringido)

    def test_reversion_ante_error_en_ambos_requerimientos(self):
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                archivos = {p for p in self.media.rglob('*') if p.is_file()}
                historial = self.contrato.historial_estados.count()
                with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('Fallo FICTICIO de auditoría')):
                    with self.assertRaises(RuntimeError):
                        self.generar(tipo)
                self.assertFalse(DocumentoGenerado.objects.filter(tipo=tipo).exists())
                self.assertEqual(archivos, {p for p in self.media.rglob('*') if p.is_file()})
                self.assertEqual(self.contrato.historial_estados.count(), historial)
                self.contrato.refresh_from_db()
                self.assertEqual(self.contrato.estado_actual.codigo, tipo)

    def test_no_infiere_documentos_ni_aseveraciones_sin_soporte(self):
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                generado = self.generar(tipo)
                self.assertIn('documentos_solicitados', generado.campos_pendientes)
                self.assertIn('consideracion_oportunidad', generado.campos_pendientes)
                self.assertIn('observaciones_pagos', generado.campos_pendientes)
        categoria = TipoDocumental.objects.create(nombre='Documento FICTICIO ya aportado', responsable=self.abogado)
        RequisitoDocumental.objects.create(tipo=categoria, clase_expediente=self.contrato.tipo_contrato, activo=True)
        documento = recibir_documento(usuario=self.abogado, contrato_id=self.contrato.pk, categoria=categoria,
            archivo=SimpleUploadedFile('FICTICIO.pdf', b'%PDF-1.7'), fecha_recepcion=None, descripcion='', operacion=uuid4())
        revisar_documento(usuario=self.abogado, contrato_id=self.contrato.pk, documento=documento, estado='conforme', estado_esperado='pendiente')
        generado = self.generar('tercer-requerimiento')
        self.assertIn('documentos_solicitados', generado.campos_pendientes)
        self.assertEqual(generado.datos_utilizados['requisitos_evaluados'][0]['estado'], 'Aportado')

    def test_preservacion_de_diseno_y_advertencia_institucional(self):
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo)
                plantilla = self.plantillas[tipo]
                with plantilla.archivo.open('rb') as archivo:
                    preparado = archivo.read()
                generado = self.generar(tipo)
                contenido = self.contenido(generado)
                with ZipFile(BytesIO(preparado)) as a, ZipFile(BytesIO(contenido)) as b:
                    self.assertEqual(set(a.namelist()), set(b.namelist()))
                    for part in a.namelist():
                        if part != 'word/document.xml':
                            self.assertEqual(a.read(part), b.read(part), part)
                    original_body = ET.fromstring(a.read('word/document.xml'))
                    nuevo_body = ET.fromstring(b.read('word/document.xml'))
                    for tag in ('sectPr', 'tbl', 'numPr', 'br', 'tab', 'footnoteReference'):
                        self.assertEqual([ET.tostring(n) for n in original_body.findall('.//'+W+tag)],
                                         [ET.tostring(n) for n in nuevo_body.findall('.//'+W+tag)], tag)
                if tipo.startswith('tercer'):
                    fuente = Path(settings.BASE_DIR).parent/'plantillas_institucionales/v1/originales/MODELO DE TERCER REQUERIMIENTO A SUPERVISORES CTO 1441-2024.docx'
                    advertencia = next(p for p in self.textos(fuente.read_bytes()) if p.startswith('Nota de alerta'))
                    self.assertIn(advertencia, self.textos(contenido))

    def test_plantilla_inactiva_no_se_sustituye_por_otro_tipo(self):
        for tipo, _, _ in TIPOS:
            with self.subTest(tipo=tipo):
                self.preparar(tipo, plantillas=False)
                with self.assertRaisesMessage(ValidationError, 'plantilla institucional activa'):
                    self.generar(tipo)
        self.assertFalse(DocumentoGenerado.objects.exists())
