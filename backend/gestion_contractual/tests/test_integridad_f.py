from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, RequestFactory
from django.urls import reverse
from gestion_contractual.integridad import ConflictoEdicion
from gestion_contractual.models import Auditoria, Contrato, DocumentoContrato, PagoContractual, OperacionPago
from gestion_contractual.liquidacion_datos import panorama
from gestion_contractual.revision import recibir_documento, revisar_documento
from gestion_contractual.archivos_transaccionales import escritura_con_archivo
from . import test_liquidacion, test_requerimientos


class IntegridadFTest(TestCase):
    setUpTestData=classmethod(test_requerimientos.RequerimientosTest.setUpTestData.__func__)
    setUp=test_liquidacion.LiquidacionTest.setUp
    pago=test_liquidacion.LiquidacionTest.pago
    insumo=test_liquidacion.LiquidacionTest.insumo
    verificado=test_liquidacion.LiquidacionTest.verificado
    generar=test_liquidacion.LiquidacionTest.generar
    texto=test_liquidacion.LiquidacionTest.texto
    root=test_liquidacion.LiquidacionTest.root
    contenido=test_liquidacion.LiquidacionTest.contenido

    def otro_contrato(self):
        otro=Contrato.objects.get(pk=self.contrato.pk);otro.pk=None;otro.numero_contrato='FICTICIO-OTRO';otro.save()
        return otro

    def fuente(self,**kwargs):
        args=dict(usuario=self.abogado,contrato_id=self.contrato.pk,categoria=self.doc.categoria,
            archivo=SimpleUploadedFile('FICTICIO.pdf',b'%PDF-1.7 fuente ficticia'),fecha_recepcion=None,descripcion='FICTICIO',operacion=uuid4())
        return recibir_documento(**(args|kwargs))

    def archivos(self):
        return {str(p) for p in Path(settings.MEDIA_ROOT).rglob('*') if p.is_file()}

    def test_F1_un_pago_dos_soportes_una_sola_suma_y_fila(self):
        a=self.pago(**self.verificado());doc2=self.fuente()
        b=self.pago(clave='P-2',naturaleza='tesoreria',operacion_economica=a.operacion_economica,**(self.verificado()|{'documento':doc2}))
        p=panorama(self.contrato)
        self.assertEqual(p['auxiliar_pagos']['valor'],'10.10')
        self.assertEqual(len(p['pagos_acreditados']),1)
        self.assertEqual({x['documento'] for x in p['pagos_acreditados'][0]['evidencias']},{self.doc.pk,doc2.pk})
        g=self.generar()
        self.assertEqual(len(g.datos_utilizados['insumos_liquidacion']['pagos']),2)
        self.assertEqual(len(g.datos_utilizados['insumos_liquidacion']['pagos_acreditados']),1)

    def test_F1_admin_registra_pago_y_segundo_soporte_sin_duplicar(self):
        self.client.force_login(self.abogado)
        url=reverse('admin:gestion_contractual_liquidacion',args=[self.contrato.pk])
        datos=dict(tipo='pago',clave='P-ADMIN-1',naturaleza='realizado',referencia='PAGO FICTICIO ADMIN',rubro='RUBRO',
            orden='ORDEN',fecha='2026-01-15',valor='10.10',certificador='TESORERIA FICTICIA',fecha_certificacion='2026-01-16',
            documento=self.doc.pk,estado='verificado',fundamento='Fuente FICTICIA',operacion=uuid4())
        self.assertEqual(self.client.post(url,datos).status_code,302)
        primero=PagoContractual.objects.get(clave='P-ADMIN-1');doc2=self.fuente()
        segundo=datos|dict(clave='P-ADMIN-2',naturaleza='tesoreria',documento=doc2.pk,operacion=uuid4(),operacion_economica=primero.operacion_economica_id)
        self.assertEqual(self.client.post(url,segundo).status_code,302)
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'10.10')

    def test_H07_duplicado_ambiguo_no_se_acredita_automaticamente(self):
        self.pago(**self.verificado())
        with self.assertRaisesMessage(ValidationError,'Posible duplicado'):
            self.pago(clave='P-2',naturaleza='tesoreria',**self.verificado())
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'10.10')

    def test_F1_potencial_duplicado_pendiente_y_resolucion_auditada(self):
        a=self.pago(**self.verificado())
        b=self.pago(clave='P-2',naturaleza='tesoreria')
        self.assertTrue(b.posible_duplicado)
        c=self.pago(clave='P-2',naturaleza='tesoreria',anterior=b,motivo='Misma operación acreditada en fuente FICTICIA',
            operacion_economica=a.operacion_economica,**self.verificado())
        self.assertFalse(c.posible_duplicado);self.assertEqual(c.version,2)
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'10.10')
        self.assertTrue(Auditoria.objects.filter(accion='rectificar_insumo_liquidacion',identificador_registro=str(c.pk)).exists())

    def test_F1_dos_pagos_distintos_igual_valor_se_suman(self):
        self.pago(**self.verificado())
        self.pago(clave='P-2',referencia='OTRO DESEMBOLSO FICTICIO',orden='OTRA ORDEN',**self.verificado())
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'20.20')

    def test_F1_dos_evidencias_misma_naturaleza_fuentes_distintas(self):
        a=self.pago(**self.verificado());doc2=self.fuente()
        self.pago(clave='P-2',operacion_economica=a.operacion_economica,**(self.verificado()|{'documento':doc2}))
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'10.10')

    def test_F1_factura_y_no_verificado_no_incrementan(self):
        self.pago(naturaleza='factura_aprobada',**self.verificado())
        self.pago(clave='P-2',referencia='OTRO FICTICIO',naturaleza='programado')
        self.assertIsNone(panorama(self.contrato)['auxiliar_pagos']['valor'])

    def test_F1_operacion_y_fuente_ajenas_rechazadas(self):
        a=self.pago(**self.verificado());otro=self.otro_contrato()
        with self.assertRaises(ValidationError):self.pago(contrato_id=otro.pk,operacion_economica=a.operacion_economica,documento=None)
        with self.assertRaises(ValidationError):self.pago(contrato_id=otro.pk,**self.verificado())

    def test_F1_reintento_conserva_operacion_e_historial(self):
        op=uuid4();a=self.pago(operacion=op,**self.verificado())
        self.assertEqual(self.pago(operacion=op,**self.verificado()),a)
        self.assertEqual(OperacionPago.objects.count(),1)
        g=self.generar()
        b=self.pago(anterior=a,motivo='Corrección FICTICIA',valor=Decimal('20.20'),**self.verificado())
        self.assertEqual(b.operacion_economica,a.operacion_economica)
        self.assertEqual(g.datos_utilizados['insumos_liquidacion']['auxiliar_pagos']['valor'],'10.10')

    def test_F1_reintento_misma_clave_con_datos_distintos_rechazado(self):
        op=uuid4();self.pago(operacion=op,**self.verificado())
        with self.assertRaises(ValidationError):self.pago(operacion=op,valor=Decimal('22'),**self.verificado())
        self.assertEqual(panorama(self.contrato)['auxiliar_pagos']['valor'],'10.10')

    def test_F1_reintento_con_clave_normalizada(self):
        op=uuid4();a=self.pago(clave=' P-FICTICIO ',operacion=op,**self.verificado())
        self.assertEqual(self.pago(clave=' P-FICTICIO ',operacion=op,**self.verificado()),a)

    def test_F1_evidencias_inconsistentes_no_se_acreditan(self):
        a=self.pago(**self.verificado())
        with self.assertRaisesMessage(ValidationError,'difieren'):
            self.pago(clave='P-2',naturaleza='tesoreria',valor=Decimal('99'),operacion_economica=a.operacion_economica,**self.verificado())

    def test_F1_modelo_no_acredita_sin_identidad(self):
        from django.utils import timezone
        with self.assertRaises(ValidationError):
            PagoContractual.objects.create(contrato=self.contrato,clave='P-MODELO',naturaleza='realizado',
                referencia='FICTICIO',orden='FICTICIO',fecha=date(2026,1,1),valor=Decimal('10.10'),
                documento=self.doc,estado='verificado',fundamento='FICTICIO',responsable=self.abogado,
                validado_por=self.abogado,validado_en=timezone.now(),operacion=uuid4())

    def test_F1_historico_sin_identidad_no_se_fusiona(self):
        a=self.pago(**self.verificado())
        # Simula fila anterior a la migración; el ORM ordinario lo prohíbe.
        from django.db import models
        models.QuerySet.update(PagoContractual.objects.filter(pk=a.pk),operacion_economica=None)
        self.assertIsNone(panorama(self.contrato)['auxiliar_pagos']['valor'])
        self.assertIn('históricos sin identidad',' '.join(panorama(self.contrato)['advertencias']))

    def test_H01_update_bulk_update_y_delete_documentales_rechazados(self):
        otro=self.otro_contrato()
        with self.assertRaises(ValidationError):DocumentoContrato.objects.filter(pk=self.doc.pk).update(contrato=otro,version=19)
        self.doc.contrato=otro
        with self.assertRaises(ValidationError):DocumentoContrato.objects.bulk_update([self.doc],['contrato'])
        with self.assertRaises(ValidationError):DocumentoContrato.objects.filter(pk=self.doc.pk).delete()
        with self.assertRaises(ValidationError):self.doc.delete()

    def test_F2_save_no_sustituye_soporte_y_revision_si_funciona(self):
        self.doc.descripcion='SUSTITUCION NO PERMITIDA'
        with self.assertRaises(ValidationError):self.doc.save()
        self.doc.refresh_from_db()
        revisar_documento(usuario=self.abogado,contrato_id=self.contrato.pk,documento=self.doc,estado='conforme',estado_esperado='pendiente')
        self.doc.refresh_from_db();self.assertEqual(self.doc.estado_revision,'conforme')

    def test_F2_rectificacion_motivo_fuente_anterior_y_auditoria(self):
        g=self.generar();antes=self.contenido(g)
        with self.assertRaises(ValidationError):self.fuente(anterior=self.doc)
        nueva=self.fuente(anterior=self.doc,motivo='Rectificación FICTICIA sustentada')
        self.assertEqual(nueva.anterior,self.doc);self.assertEqual(nueva.version,2)
        evento=Auditoria.objects.filter(accion='recibir_documento',identificador_registro=str(nueva.pk)).get()
        self.assertEqual(evento.datos_nuevos['motivo'],nueva.motivo_rectificacion)
        self.assertEqual(self.contenido(g),antes)
        self.assertTrue(self.doc.archivo.storage.exists(self.doc.archivo.name))
        self.client.force_login(self.abogado)
        respuesta=self.client.post(reverse('admin:gestion_contractual_revision_documental',args=[self.contrato.pk]),{
            'accion':'recibir','operacion':uuid4(),'categoria':self.doc.categoria_id,'anterior':nueva.pk,
            'archivo':SimpleUploadedFile('FICTICIO-v3.pdf',b'%PDF-1.7 version ficticia'),'descripcion':'FICTICIO',
            'motivo':'Rectificación FICTICIA desde Admin'})
        self.assertEqual(respuesta.status_code,302)
        self.assertEqual(DocumentoContrato.objects.get(anterior=nueva).version,3)

    def test_F2_revision_de_version_legacy_no_inventa_motivo(self):
        from django.db import models
        doc=self.fuente(anterior=self.doc,motivo='Motivo FICTICIO')
        # Representa datos previos a 0015, no se actualizan durante la migración.
        models.QuerySet.update(DocumentoContrato.objects.filter(pk=doc.pk),motivo_rectificacion='')
        doc.refresh_from_db()
        revisar_documento(usuario=self.abogado,contrato_id=self.contrato.pk,documento=doc,estado='conforme',estado_esperado='pendiente')
        doc.refresh_from_db()
        self.assertEqual((doc.estado_revision,doc.motivo_rectificacion),('conforme',''))

    def test_H02_admin_error_auditoria_no_deja_archivo(self):
        doc=DocumentoContrato(contrato=self.contrato,tipo_documento='FICTICIO',nombre_original='FICTICIO.pdf',
            archivo=SimpleUploadedFile('FICTICIO.pdf',b'%PDF-1.7'),cargado_por=self.abogado)
        req=RequestFactory().post('/admin/');req.user=self.abogado;antes=self.archivos()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):admin.site._registry[DocumentoContrato].save_model(req,doc,SimpleNamespace(changed_data=['archivo']),False)
        self.assertEqual(self.archivos(),antes)

    def test_F3_admin_post_error_posterior_a_auditoria_compensa(self):
        self.client.force_login(self.abogado);antes=self.archivos();eventos=Auditoria.objects.count()
        sitio=admin.site._registry[DocumentoContrato]
        with patch.object(sitio,'log_addition',side_effect=RuntimeError('Fallo FICTICIO final Admin')):
            with self.assertRaises(RuntimeError):self.client.post(reverse('admin:gestion_contractual_documentocontrato_add'),{
                'contrato':self.contrato.pk,'tipo_documento':'FICTICIO','nombre_original':'FICTICIO.pdf',
                'archivo':SimpleUploadedFile('FICTICIO.pdf',b'%PDF-1.7'),'activo':'on','categoria':self.doc.categoria_id})
        self.assertEqual(self.archivos(),antes)
        self.assertEqual(Auditoria.objects.count(),eventos)

    def test_H08_escritura_parcial_compensada(self):
        antes=self.archivos()
        def parcial(storage,name,content):
            p=Path(storage.path(name));p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'FICTICIO PARCIAL')
            raise OSError('Fallo FICTICIO parcial')
        with patch.object(FileSystemStorage,'_save',parcial):
            with self.assertRaises(OSError):self.fuente()
        self.assertEqual(self.archivos(),antes)

    def test_F3_error_antes_commit_compensa_y_preserva_historico(self):
        antes=self.archivos()
        with self.assertRaises(RuntimeError),escritura_con_archivo():
            nueva=self.fuente();raise RuntimeError('Fallo FICTICIO al final de transacción')
        self.assertEqual(self.archivos(),antes)
        self.assertFalse(DocumentoContrato.objects.filter(pk=nueva.pk).exists())
        self.assertTrue(self.doc.archivo.storage.exists(self.doc.archivo.name))

    def test_F3_docx_error_auditoria_reintento_seguro(self):
        antes=self.archivos();op=uuid4()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):self.generar(operacion=op)
        self.assertEqual(self.archivos(),antes)
        g=self.generar(operacion=op)
        self.assertEqual(self.generar(operacion=op),g)

    def test_F3_commit_finaliza_manifiesto(self):
        with self.captureOnCommitCallbacks(execute=True):doc=self.fuente()
        self.assertFalse((Path(doc.archivo.path).parent/'operacion-pendiente.json').exists())
        self.assertTrue(Path(doc.archivo.path).exists())

    def test_F3_dos_operaciones_no_compensan_archivo_ajeno(self):
        primero=self.fuente();antes=Path(primero.archivo.path).read_bytes()
        with self.assertRaises(RuntimeError),escritura_con_archivo():
            self.fuente();raise RuntimeError('Fallo FICTICIO')
        self.assertEqual(Path(primero.archivo.path).read_bytes(),antes)

    def test_H03_primera_edicion_valida_segunda_obsoleta_rechazada(self):
        a=Contrato.objects.get(pk=self.contrato.pk);b=Contrato.objects.get(pk=a.pk)
        a.supervisor_nombre='SUPERVISOR FICTICIO NUEVO';a.save()
        b.refresh_from_db(fields=['version_registro'])
        b.objeto_contractual='NO DEBE SOBRESCRIBIR'
        with self.assertRaises(ConflictoEdicion):b.save()
        b.refresh_from_db();self.assertEqual(b.supervisor_nombre,a.supervisor_nombre)
        b.objeto_contractual='EDICION FICTICIA REVISADA';b.save()
        self.assertEqual(b.version_registro,a.version_registro+1)

    def test_H06_update_contractual_y_masivo_rechazados(self):
        with self.assertRaises(ValidationError):Contrato.objects.filter(pk=self.contrato.pk).update(fecha_terminacion=date(2000,1,1))
        with self.assertRaises(ValidationError):Contrato.objects.bulk_update([self.contrato],['objeto_contractual'])

    def test_F4_contratos_distintos_y_estado_preservado(self):
        a=Contrato.objects.get(pk=self.contrato.pk);otro=self.otro_contrato()
        otro.objeto_contractual='OTRO FICTICIO';otro.save()
        a.objeto_contractual='PRIMERO FICTICIO';a.save()
        self.assertEqual(a.estado_actual_id,self.estado.pk)

    def datos_formulario(self,version):
        c=self.contrato
        return dict(entidad=c.entidad_id,numero_contrato=c.numero_contrato,anio_contractual=c.anio_contractual,
            objeto_contractual='OBJETO FICTICIO ADMIN',tipo_contrato=c.tipo_contrato,modalidad_contratacion=c.modalidad_contratacion,
            contratista_nombre=c.contratista_nombre,contratista_identificacion=c.contratista_identificacion,
            valor_inicial=c.valor_inicial,valor_actual=c.valor_actual,fecha_suscripcion=c.fecha_suscripcion.isoformat(),version_esperada=version)

    def test_F4_admin_muestra_conflicto_sin_auditoria_exitosa(self):
        version=self.contrato.version_registro
        nueva=Contrato.objects.get(pk=self.contrato.pk);nueva.supervisor_nombre='OTRO CAMBIO FICTICIO';nueva.save()
        self.client.force_login(self.abogado);antes=Auditoria.objects.count()
        r=self.client.post(reverse('admin:gestion_contractual_contrato_change',args=[nueva.pk]),self.datos_formulario(version))
        self.assertContains(r,'Conflicto de edición')
        self.assertEqual(Auditoria.objects.count(),antes)
        nueva.refresh_from_db();self.assertEqual(nueva.supervisor_nombre,'OTRO CAMBIO FICTICIO')

    def test_F4_admin_edicion_confirmada_audita_valores(self):
        self.client.force_login(self.abogado)
        r=self.client.post(reverse('admin:gestion_contractual_contrato_change',args=[self.contrato.pk]),self.datos_formulario(self.contrato.version_registro))
        self.assertEqual(r.status_code,302)
        e=Auditoria.objects.filter(accion='editar_contrato').get()
        self.assertEqual(e.datos_anteriores['objeto_contractual'],self.contrato.objeto_contractual)
        self.assertEqual(e.datos_nuevos['objeto_contractual'],'OBJETO FICTICIO ADMIN')

    def test_F4_fallo_auditoria_revierte_version_y_edicion(self):
        self.client.force_login(self.abogado);version=self.contrato.version_registro;objeto=self.contrato.objeto_contractual
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):self.client.post(reverse('admin:gestion_contractual_contrato_change',args=[self.contrato.pk]),self.datos_formulario(version))
        self.contrato.refresh_from_db()
        self.assertEqual((self.contrato.version_registro,self.contrato.objeto_contractual),(version,objeto))
