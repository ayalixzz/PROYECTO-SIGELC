"""Acta institucional: solo expedientes ficticios y transacciones aisladas."""
from datetime import date
from decimal import Decimal
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
from django.db import IntegrityError, models, transaction
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.competencia import registrar_calculo
from gestion_contractual.liquidacion import ROTULO, contexto
from gestion_contractual.liquidacion_datos import registrar_insumo, panorama, vigentes, dinero
from gestion_contractual.models import Auditoria, DatoLiquidacion, DocumentoGenerado, GarantiaContractual, PagoContractual, TipoActa, TipoDocumental
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.revision import recibir_documento
from . import test_requerimientos

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class LiquidacionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        from .calendarios_ficticios import aprobar_regresion
        aprobar_regresion(cls.abogado)

    def setUp(self):
        test_requerimientos.RequerimientosTest.setUp(self)
        self.contrato.numero_contrato='FICTICIO-LIQ-003'
        self.contrato.objeto_contractual='Objeto FICTICIO de prueba documental, sin validez contractual.'
        self.contrato.supervisor_nombre='Supervisor FICTICIO sin firma'
        self.contrato.fecha_inicio=date(2026,1,5)
        self.contrato.fecha_terminacion=date(2026,3,31)
        self.contrato.save()
        self.abogado.first_name='Abogado FICTICIO'
        self.abogado.save()
        self.path=Path(settings.BASE_DIR).parent/'plantillas_institucionales/liquidacion/v1/ACTA-LIQUIDACION-INSTITUCIONAL-v1.docx'
        self.tipo=TipoActa.objects.create(nombre='Liquidación contractual')
        self.plantilla=registrar_plantilla(usuario=self.abogado,tipo=self.tipo.clave_documental,tipo_acta=self.tipo,
            nombre='ACTA DE LIQUIDACION institucional FICTICIA',archivo=SimpleUploadedFile(self.path.name,self.path.read_bytes()),activar=True)
        categoria=TipoDocumental.objects.create(nombre='Fuente FICTICIA de liquidación',responsable=self.abogado)
        self.doc=recibir_documento(usuario=self.abogado,contrato_id=self.contrato.pk,categoria=categoria,
            archivo=SimpleUploadedFile('fuente-FICTICIA.pdf',b'%PDF-1.7'),fecha_recepcion=None,
            descripcion='Fuente FICTICIA para pruebas, no certifica hechos reales.',operacion=uuid4())

    def insumo(self,tipo='dato',**kwargs):
        args=dict(usuario=self.abogado,contrato_id=self.contrato.pk,tipo=tipo,operacion=uuid4())
        return registrar_insumo(**(args|kwargs))

    def dato(self,clave='saldo',clase='balance',**kwargs):
        args=dict(clave=clave,clase=clase)
        return self.insumo(**(args|kwargs))

    def verificado(self):
        return dict(estado='verificado',documento=self.doc,fundamento='Referencia FICTICIA página 1')

    def garantia(self,**kwargs):
        args=dict(clave='G-1',seccion='principal',aseguradora='Aseguradora FICTICIA',poliza='POL-FICTICIA-1',anexo='ANEXO-FICTICIO-1',
            fecha_expedicion=date(2026,1,1),fecha_aprobacion=date(2026,1,2),amparo='Amparo FICTICIO',valor_asegurado=Decimal('200.50'),
            vigencia_inicio=date(2026,1,1),vigencia_fin=date(2026,12,31))
        return self.insumo('garantia',**(args|kwargs))

    def pago(self,**kwargs):
        args=dict(clave='P-1',naturaleza='realizado',referencia='PAGO-FICTICIO-1',rubro='RUBRO-FICTICIO',orden='ORDEN-FICTICIA-1',
            fecha=date(2026,2,1),valor=Decimal('10.10'),certificador='Tesorería FICTICIA',fecha_certificacion=date(2026,2,2))
        return self.insumo('pago',**(args|kwargs))

    def generar(self,**kwargs):
        args=dict(usuario=self.abogado,contrato_id=self.contrato.pk,tipo='',tipo_acta_id=self.tipo.pk,plantilla_id=self.plantilla.pk,operacion=uuid4())
        return generar_requerimiento(**(args|kwargs))

    def contenido(self,g):
        with g.documento.archivo.open('rb') as f:return f.read()

    def root(self,b):
        with ZipFile(BytesIO(b)) as z:return E.fromstring(z.read('word/document.xml'))

    def texto(self,g):
        return '\n'.join(''.join(p.itertext()) for p in self.root(self.contenido(g)).iter(W+'p'))

    def test_campos_contractuales_y_pendientes_sin_copiar_ejemplo(self):
        g=self.generar();texto=self.texto(g)
        for texto_esperado in (ROTULO,'FICTICIO-LIQ-003','Objeto FICTICIO','Supervisor FICTICIO','[PENDIENTE','sin suscripción ni formalización'):
            self.assertIn(texto_esperado,texto)
        for ejemplo in ('1541','PASCUAL','SHIRLEY','2024','91.830.270'):
            self.assertNotIn(ejemplo,texto)
        self.assertNotIn('{{',texto)
        self.assertIsNone(g.calculo_competencia)
        self.assertTrue(g.datos_utilizados['liquidacion_institucional'])

    def test_representacion_solo_fuentes_verificadas(self):
        self.dato('responsable_entidad_nombre','representacion',texto='NOMBRE NO VERIFICADO')
        g=self.generar();self.assertNotIn('NOMBRE NO VERIFICADO',self.texto(g))
        anterior=DatoLiquidacion.objects.get(clave='responsable_entidad_nombre')
        self.dato('responsable_entidad_nombre','representacion',anterior=anterior,motivo='Revisión FICTICIA',texto='Responsable FICTICIO',
                  emisor='Emisor FICTICIO',fecha_fuente=date(2026,1,1),**self.verificado())
        self.assertIn('Responsable FICTICIO',self.texto(self.generar()))
        self.assertNotIn('Responsable FICTICIO',self.texto(g))

    def test_garantias_multiples_polizas_anexos_y_amparos(self):
        self.garantia(**self.verificado())
        self.garantia(clave='G-2',amparo='Segundo amparo FICTICIO',**self.verificado())
        self.garantia(clave='G-3',poliza='POL-FICTICIA-2',anexo='ANEXO-FICTICIO-2',**self.verificado())
        self.garantia(clave='G-4',seccion='extension',anexo='ANEXO-FICTICIO-3',**self.verificado())
        g=self.generar();texto=self.texto(g)
        for esperado in ('POL-FICTICIA-1','POL-FICTICIA-2','ANEXO-FICTICIO-3','Segundo amparo FICTICIO','$ 200,50'):
            self.assertIn(esperado,texto)
        self.assertEqual(len(g.datos_utilizados['insumos_liquidacion']['garantias']),4)
        self.assertEqual(len(self.root(self.contenido(g)).find('.//'+W+'tbl').findall(W+'tr')),40)

    def test_garantia_pendiente_no_se_declara_vigente(self):
        self.garantia(aseguradora='ASEGURADORA SIN VERIFICAR')
        g=self.generar();self.assertNotIn('ASEGURADORA SIN VERIFICAR',self.texto(g))
        self.assertIn('suficiencia y aplicabilidad requieren revisión',self.texto(g))
        self.assertIn('garantias_',str(g.datos_utilizados['pendientes_documentales']))

    def test_mismo_anexo_amparos_con_revision_mixta(self):
        self.garantia(**self.verificado())
        self.garantia(clave='G-2',amparo='AMPARO NO REVISADO')
        texto=self.texto(self.generar())
        self.assertIn('POL-FICTICIA-1',texto)
        self.assertNotIn('AMPARO NO REVISADO',texto)

    def test_garantia_requiere_soporte_completitud_y_fechas(self):
        for kwargs in (dict(estado='verificado'),dict(**self.verificado(),anexo=''),dict(vigencia_fin=date(2025,1,1)),dict(valor_asegurado=Decimal('-1'))):
            with self.assertRaises(ValidationError):self.garantia(**kwargs)
        self.assertFalse(GarantiaContractual.objects.exists())

    def test_garantia_rectificacion_conserva_anterior(self):
        a=self.garantia(**self.verificado());viejo=self.generar()
        b=self.garantia(anterior=a,motivo='Ajuste FICTICIO',valor_asegurado=Decimal('300.25'),**self.verificado())
        nuevo=self.generar()
        self.assertEqual((b.version,b.anterior_id),(2,a.pk))
        self.assertEqual(list(vigentes(GarantiaContractual,self.contrato)),[b])
        self.assertIn('$ 200,50',self.texto(viejo));self.assertIn('$ 300,25',self.texto(nuevo))
        self.assertEqual(viejo.datos_utilizados['insumos_liquidacion']['garantias'][0]['version'],1)

    def test_duplicados_clave_y_referencia_rechazados(self):
        self.garantia()
        with self.assertRaises(ValidationError):self.garantia()
        with self.assertRaises(ValidationError):self.garantia(clave='OTRA')
        self.pago()
        with self.assertRaises(ValidationError):self.pago(clave='OTRO')

    def test_pago_varios_decimales_exactos_y_referencias(self):
        self.pago(**self.verificado())
        self.pago(clave='P-2',referencia='PAGO-FICTICIO-2',orden='ORDEN-FICTICIA-2',naturaleza='tesoreria',valor=Decimal('20.20'),**self.verificado())
        g=self.generar();p=g.datos_utilizados['insumos_liquidacion']
        self.assertEqual(p['auxiliar_pagos']['valor'],'30.30')
        self.assertEqual(len(p['auxiliar_pagos']['ids']),2)
        for esperado in ('ORDEN-FICTICIA-1','ORDEN-FICTICIA-2','RUBRO-FICTICIO','$ 30,30','2026-02-01'):
            self.assertIn(esperado,self.texto(g))

    def test_facturas_programados_y_no_verificados_no_son_pago(self):
        for n,naturaleza in enumerate(('factura_emitida','factura_aprobada','programado','realizado'),1):
            datos=self.verificado() if n<4 else {}
            self.pago(clave=f'P-{n}',referencia=f'REF-{n}',naturaleza=naturaleza,**datos)
        g=self.generar();p=g.datos_utilizados['insumos_liquidacion']
        self.assertEqual(p['pagos_acreditados'],[])
        self.assertIsNone(p['auxiliar_pagos']['valor'])
        self.assertNotIn('ORDEN-FICTICIA-1',self.texto(g))
        self.assertIn('facturas y pagos programados no se incluyen',self.texto(g))

    def test_pago_verificado_exige_certificacion_y_soporte(self):
        for kwargs in (dict(estado='verificado'),dict(**self.verificado(),certificador=''),dict(**self.verificado(),fecha_certificacion=None),dict(**self.verificado(),valor=None)):
            with self.assertRaises(ValidationError):self.pago(**kwargs)

    def test_balance_desconocido_no_cero_y_cero_sustentado(self):
        self.assertIsNone(panorama(self.contrato)['auxiliar_pagos']['valor'])
        self.dato(valor=Decimal('0'),**self.verificado())
        g=self.generar();campos=g.datos_utilizados['campos']
        self.assertEqual(campos['balance_saldo'],'$ 0,00')
        self.assertEqual(campos['balance_reintegro'],'[PENDIENTE: verificación financiera]')
        self.assertIn('pendiente de verificación financiera',campos['valor_inicial_registrado'])
        self.assertEqual(campos['declaracion_paz_salvo'],'[PENDIENTE: declaración sustentada y revisada]')

    def test_balance_diferencias_avisadas_sin_inferir_saldo(self):
        self.pago(**self.verificado())
        for clave,valor in [('valor_inicial','100'),('adiciones','20'),('total','130'),('pagado','99')]:
            self.dato(clave,valor=Decimal(valor),**self.verificado())
        p=panorama(self.contrato)
        self.assertEqual(sum('Diferencia' in a for a in p['advertencias']),2)
        self.assertIn('verificación financiera',self.generar().datos_utilizados['campos']['balance_saldo'])

    def test_precision_no_redondeo_silencioso_ni_negativos(self):
        for valor in ('1.001','-0.01'):
            with self.assertRaises(ValidationError):self.pago(valor=Decimal(valor),**self.verificado())
            with self.assertRaises(ValidationError):self.dato(valor=Decimal(valor),**self.verificado())
        self.assertEqual(dinero(Decimal('1234567.89')),'$ 1.234.567,89')

    def test_declaraciones_revisadas_con_fuente_no_acuerdo_consumado(self):
        for clave in ('cumplimiento','seguridad_social','sanciones','ecuacion','reclamaciones','paz_salvo'):
            self.dato(clave,'declaracion',texto=f'TEXTO FICTICIO PROPUESTO {clave}',emisor='Emisor FICTICIO',fecha_fuente=date(2026,1,1),**self.verificado())
        g=self.generar();texto=self.texto(g)
        self.assertEqual(texto.count('Texto propuesto revisado, pendiente de adopción y suscripción'),6)
        self.assertIn('fuente #',texto)
        self.assertIn('sin suscripción ni formalización',texto)

    def test_declaracion_no_verificada_o_sin_fuente_no_se_afirma(self):
        self.dato('paz_salvo','declaracion',texto='NO DEBE AFIRMARSE PAZ Y SALVO')
        self.assertNotIn('NO DEBE AFIRMARSE',self.texto(self.generar()))
        with self.assertRaises(ValidationError):
            self.dato('cumplimiento','declaracion',texto='NO',estado='verificado')

    def test_clave_clase_y_importe_ajenos_rechazados(self):
        for clave,clase,datos in [('inventado','balance',{}),('saldo','declaracion',{}),('sanciones','declaracion',{'valor':Decimal('1')})]:
            with self.assertRaises(ValidationError):self.dato(clave,clase,**datos)

    def test_calculo_solo_verificado_vigente_y_referencia_version(self):
        self.contrato.fecha_inicio=date(2025,1,1)
        self.contrato.fecha_terminacion=date(2025,2,12);self.contrato.save()
        args=dict(contrato_id=self.contrato.pk,usuario=self.abogado)
        a=registrar_calculo(**args,operacion=uuid4(),anterior_id=None,datos={'sujeto':'si','regimen':'art11_cpaca','origen':'ejecucion','pacto':'no','liquidacion':'ninguna','incidencias':'no'})
        self.assertIsNone(self.generar().calculo_competencia)
        b=registrar_calculo(**args,operacion=uuid4(),anterior_id=a.pk,accion='verificar',fundamento='Fundamento FICTICIO')
        g=self.generar();self.assertEqual(g.calculo_competencia,b)
        self.assertEqual(g.datos_utilizados['calculo_origen']['version'],b.version)
        self.contrato.fecha_terminacion=date(2025,2,13);self.contrato.save()
        self.assertIsNone(self.generar().calculo_competencia)
        self.assertIn('2027-08-12',self.texto(g))

    def test_docx_preserva_graficos_estilos_margenes_y_celdas(self):
        g=self.generar()
        with ZipFile(self.path) as a,ZipFile(BytesIO(self.contenido(g))) as b:
            for nombre in a.namelist():
                if nombre.startswith('word/media/') or nombre in ('word/styles.xml','word/numbering.xml','word/fontTable.xml'):
                    self.assertEqual(a.read(nombre),b.read(nombre),nombre)
            ra=E.fromstring(a.read('word/document.xml'));rb=E.fromstring(b.read('word/document.xml'))
            self.assertEqual(E.tostring(ra.find('.//'+W+'sectPr')),E.tostring(rb.find('.//'+W+'sectPr')))
            for etiqueta in ('br','pageBreakBefore'):
                originales=[E.tostring(e) for e in ra.findall('.//'+W+etiqueta) if etiqueta!='br' or e.get(W+'type')=='page']
                generados=[E.tostring(e) for e in rb.findall('.//'+W+etiqueta) if etiqueta!='br' or e.get(W+'type')=='page']
                self.assertEqual(originales,generados)
            self.assertGreater(len(rb.findall('.//'+W+'gridSpan')),0)
            for nombre in b.namelist():
                if nombre.startswith('word/header') or nombre.startswith('word/footer'):
                    texto=b.read(nombre).decode();self.assertNotIn('{{',texto)
                    self.assertNotIn('1541',texto)

    def test_historial_inmutable_y_restricciones_en_postgresql(self):
        a=self.garantia()
        with self.assertRaises(ValidationError):GarantiaContractual.objects.filter(pk=a.pk).update(amparo='CAMBIO')
        with self.assertRaises(ValidationError):a.delete()
        a.amparo='CAMBIO'
        with self.assertRaises(ValidationError):a.save()
        datos=dict(contrato=self.contrato,clave='DB',responsable=self.abogado,version=1,operacion=uuid4(),naturaleza='realizado',referencia='DB',valor=Decimal('-1'))
        with self.assertRaises(IntegrityError),transaction.atomic():
            models.QuerySet.bulk_create(PagoContractual.objects.all(),[PagoContractual(**datos)])

    def test_soportes_mismo_expediente_y_no_autocertificacion(self):
        otro=type(self.contrato).objects.get(pk=self.contrato.pk);otro.pk=None;otro.numero_contrato='FICTICIO-AJENO';otro.save()
        for tipo,datos in [('garantia',dict(clave='G')),('pago',dict(clave='P',referencia='P')),('dato',dict(clave='saldo',clase='balance'))]:
            with self.assertRaises(ValidationError):self.insumo(tipo,contrato_id=otro.pk,documento=self.doc,**datos)
        g=self.generar()
        with self.assertRaises(ValidationError):self.pago(documento=g.documento)

    def test_reintentos_no_duplican_registros_ni_actas(self):
        op=uuid4();a=self.pago(operacion=op);self.assertEqual(self.pago(operacion=op),a)
        op=uuid4();g=self.generar(operacion=op);self.assertEqual(self.generar(operacion=op),g)
        self.assertEqual(PagoContractual.objects.count(),1)
        self.assertEqual(DocumentoGenerado.objects.count(),1)

    def test_pago_rectificado_no_altera_instantanea_anterior(self):
        a=self.pago(**self.verificado());g=self.generar()
        b=self.pago(anterior=a,motivo='Rectificación FICTICIA de valor',valor=Decimal('20.20'),**self.verificado())
        nuevo=self.generar()
        self.assertEqual(b.version,2)
        self.assertEqual(g.datos_utilizados['insumos_liquidacion']['auxiliar_pagos']['valor'],'10.10')
        self.assertEqual(nuevo.datos_utilizados['insumos_liquidacion']['auxiliar_pagos']['valor'],'20.20')
        self.assertEqual(g.datos_utilizados['insumos_liquidacion']['pagos'][0]['documento_fuente']['id'],self.doc.pk)

    def test_gcof19_borrador_y_obligaciones_no_certifican_cumplimiento(self):
        from gestion_contractual.obligaciones import registrar_obligacion
        from gestion_contractual.revision import registrar_observacion
        path=Path(settings.BASE_DIR).parent/'plantillas_institucionales/gcof19/reconstruccion-v1/GCOF19-PLANTILLA-RECONSTRUIDA-PARA-REVISION.docx'
        registrar_plantilla(usuario=self.abogado,tipo='gcof19',nombre='GCOF19 FICTICIO',archivo=SimpleUploadedFile(path.name,path.read_bytes()),activar=True)
        ob=registrar_obligacion(usuario=self.abogado,contrato_id=self.contrato.pk,numero=1,tipo='general',descripcion='Obligación FICTICIA',operacion=uuid4())
        obs=registrar_observacion(usuario=self.abogado,contrato_id=self.contrato.pk,descripcion='Observación FICTICIA',tipo='otro',estado_revision='pendiente',evidencia='Ref FICTICIA',documentos=[self.doc],operacion=uuid4())
        previo=generar_requerimiento(usuario=self.abogado,contrato_id=self.contrato.pk,tipo='gcof19',operacion=uuid4())
        g=self.generar()
        self.assertEqual(g.datos_utilizados['referencias_gcof19'][0]['id'],previo.pk)
        self.assertEqual(g.datos_utilizados['obligaciones_origen'][0]['id'],ob.pk)
        self.assertEqual(g.datos_utilizados['observaciones_origen'][0]['id'],obs.pk)
        self.assertIn('1 borradores GCOF19 (no certifican cumplimiento)',self.texto(g))
        self.assertEqual(g.datos_utilizados['campos']['declaracion_cumplimiento'],'[PENDIENTE: declaración sustentada y revisada]')

    def test_permiso_operativo_de_documentos_obligatorio(self):
        from django.contrib.auth.models import Permission
        self.abogado.user_permissions.remove(Permission.objects.get(content_type__app_label='gestion_contractual',codename='change_documentocontrato'))
        self.abogado=type(self.abogado).objects.get(pk=self.abogado.pk)
        with self.assertRaises(PermissionDenied):self.pago()
        self.client.force_login(self.abogado)
        url=reverse('admin:gestion_contractual_liquidacion',args=[self.contrato.pk])
        self.assertContains(self.client.get(url),'Historial')
        self.assertEqual(self.client.post(url,{'tipo':'dato'}).status_code,403)

    def test_auditoria_error_revierte_insumo(self):
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo simulado')):
            with self.assertRaises(RuntimeError):self.pago()
        self.assertFalse(PagoContractual.objects.exists())

    def test_error_almacenamiento_y_auditoria_sin_archivos_huerfanos(self):
        antes={str(p) for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()}
        with patch('django.core.files.storage.FileSystemStorage._save',side_effect=OSError('Fallo simulado')):
            with self.assertRaises(OSError):self.generar()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo simulado')):
            with self.assertRaises(RuntimeError):self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual(antes,{str(p) for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()})

    def test_revision_manual_version_motivo_y_rotulo(self):
        g=self.generar()
        archivo=SimpleUploadedFile('revision.docx',self.contenido(g))
        with self.assertRaises(ValidationError):self.generar(archivo_revision=archivo,anterior_id=g.pk,motivo='')
        nuevo=self.generar(archivo_revision=SimpleUploadedFile('revision.docx',self.contenido(g)),anterior_id=g.pk,motivo='Revisión FICTICIA')
        self.assertEqual(nuevo.version,2);self.assertEqual(nuevo.anterior,g)
        self.assertIn(ROTULO,self.texto(nuevo))
        self.assertTrue(Auditoria.objects.filter(identificador_registro=str(nuevo.pk)).exists())
        sin_rotulo=self.generar(archivo_revision=test_requerimientos.docx(),anterior_id=nuevo.pk,motivo='Prueba FICTICIA del rótulo obligatorio')
        self.assertIn(ROTULO,self.texto(sin_rotulo))

    def test_admin_previsualizacion_advertencias_y_generacion(self):
        self.client.force_login(self.abogado)
        url=reverse('admin:gestion_contractual_liquidacion',args=[self.contrato.pk])
        r=self.client.get(url);self.assertEqual(r.status_code,200)
        self.assertContains(r,'No hay pagos efectivamente realizados acreditados')
        self.assertContains(r,self.plantilla.nombre)
        self.assertEqual(self.client.post(url,dict(tipo='dato',clave='saldo',clase='balance',operacion=uuid4(),estado='pendiente')).status_code,302)
        self.assertTrue(DatoLiquidacion.objects.filter(clave='saldo').exists())
        url=reverse('admin:gestion_contractual_requerimientos',args=[self.contrato.pk])
        datos=dict(tipo_acta=self.tipo.pk,plantilla=self.plantilla.pk,operacion=uuid4())
        self.assertContains(self.client.post(url,datos|{'previsualizar':'1'}),'verificación financiera')
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual(self.client.post(url,datos).status_code,302)
        self.assertEqual(DocumentoGenerado.objects.count(),1)

    def test_autenticacion_permisos_y_descarga_privada(self):
        g=self.generar();url=reverse('descargar_documento',args=[g.documento_id])
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.otro);self.assertEqual(self.client.get(url).status_code,403)
        with self.assertRaises(PermissionDenied):self.pago(usuario=self.otro)
        with self.assertRaises(PermissionDenied):self.generar(usuario=self.otro)
        self.client.force_login(self.abogado);r=self.client.get(url);self.assertEqual(r.status_code,200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:self.assertTrue(b''.join(r.streaming_content).startswith(b'PK'))
        finally:signals.request_finished.connect(close_old_connections)

    def test_generacion_nunca_cambia_estado_ni_transiciones(self):
        from gestion_contractual.models import TransicionEstado
        matriz=list(TransicionEstado.objects.values())
        self.generar();self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual,self.estado)
        self.assertFalse(self.contrato.historial_estados.exists())
        self.assertEqual(list(TransicionEstado.objects.values()),matriz)

    def test_muestra_ficticia_balance_consistente_y_declaraciones_pendientes(self):
        self.contrato.contratista_nombre='Contratista FICTICIO de demostración'
        self.contrato.contratista_identificacion='ID-FICTICIO-003'
        self.contrato.valor_inicial=Decimal('1000.00');self.contrato.save()
        for clave,texto in [('responsable_entidad_nombre','Responsable FICTICIO'),('responsable_entidad_id','ID-FICTICIO-E'),('cargo_entidad','Cargo FICTICIO'),
                            ('facultades_entidad','Documento FICTICIO de facultades, solo prueba'),('representante_legal','Representante FICTICIO'),
                            ('representante_id','ID-FICTICIO-R'),('facultades_contratista','Certificado FICTICIO de representación, solo prueba')]:
            self.dato(clave,'representacion',texto=texto,emisor='Emisor FICTICIO',fecha_fuente=date(2026,1,1),**self.verificado())
        self.garantia(**self.verificado())
        self.garantia(clave='G-2',amparo='Calidad FICTICIA',**self.verificado())
        self.garantia(clave='G-3',seccion='extension',anexo='EXT-FICTICIA',**self.verificado())
        self.pago(valor=Decimal('400.00'),**self.verificado())
        self.pago(clave='P-2',referencia='PAGO-FICTICIO-2',orden='ORDEN-FICTICIA-2',valor=Decimal('600.00'),naturaleza='tesoreria',**self.verificado())
        for clave,valor in [('valor_inicial','1000'),('adiciones','0'),('total','1000'),('ejecutado','1000'),('pagado','1000'),('saldo','0'),('reintegro','0'),('liberado','0')]:
            self.dato(clave,valor=Decimal(valor),**self.verificado())
        self.dato('cumplimiento','declaracion',texto='Propuesta FICTICIA NO VERIFICADA')
        g=self.generar()
        self.assertEqual(g.datos_utilizados['insumos_liquidacion']['auxiliar_pagos']['valor'],'1000.00')
        self.assertFalse(any('Diferencia' in a for a in panorama(self.contrato)['advertencias']))
        self.assertNotIn('Propuesta FICTICIA NO VERIFICADA',self.texto(g))
        self.assertIn('declaración sustentada y revisada',self.texto(g))
        if os.environ.get('SIGELC_MUESTRA_LIQUIDACION'):
            carpeta=Path(os.environ['SIGELC_MUESTRA_LIQUIDACION'])
            (carpeta/'PRUEBA-FICTICIA-acta-liquidacion.docx').write_bytes(self.contenido(g))
            import json
            (carpeta/'muestra-procedencia.json').write_text(json.dumps(g.datos_utilizados,ensure_ascii=False,indent=2),encoding='utf-8')
