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
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, models, transaction
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.gcof19 import ROTULO, preparar_gcof19
from gestion_contractual.models import Auditoria, DocumentoGenerado, HallazgoObligacion, ObligacionContractual, SoporteObligacion, TipoDocumental
from gestion_contractual.obligaciones import registrar_obligacion, vigentes
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from gestion_contractual.revision import recibir_documento, registrar_observacion
from . import test_requerimientos

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class Gcof19Test(TestCase):
    setUpTestData = classmethod(test_requerimientos.RequerimientosTest.setUpTestData.__func__)

    def setUp(self):
        test_requerimientos.RequerimientosTest.setUp(self)
        self.contrato.numero_contrato = 'FICTICIO-GCOF-002'
        self.contrato.objeto_contractual = 'Objeto FICTICIO de integración GCOF19, sin validez contractual.'
        self.contrato.supervisor_nombre = 'Supervisor FICTICIO sin firma'
        self.contrato.fecha_inicio, self.contrato.fecha_terminacion = date(2026,1,5), date(2026,3,31)
        self.contrato.save()
        self.path = Path(settings.BASE_DIR).parent/'plantillas_institucionales/gcof19/reconstruccion-v1/GCOF19-PLANTILLA-RECONSTRUIDA-PARA-REVISION.docx'
        self.plantilla = self.registrar_plantilla()

    def registrar_plantilla(self):
        return registrar_plantilla(usuario=self.abogado,tipo='gcof19',nombre='GCOF19 aprobado — prueba FICTICIA',
            archivo=SimpleUploadedFile(self.path.name,self.path.read_bytes()),activar=True)

    def obligacion(self, **kwargs):
        datos=dict(usuario=self.abogado,contrato_id=self.contrato.pk,numero=1,tipo='general',
                   descripcion='Obligación FICTICIA A',operacion=uuid4())
        datos.update(kwargs)
        return registrar_obligacion(**datos)

    def soporte(self):
        tipo=TipoDocumental.objects.create(nombre='Soporte FICTICIO GCOF19',responsable=self.abogado)
        return recibir_documento(usuario=self.abogado,contrato_id=self.contrato.pk,categoria=tipo,
            archivo=SimpleUploadedFile('soporte-FICTICIO-GCOF19.pdf',b'%PDF-1.7'),fecha_recepcion=None,
            descripcion='Soporte FICTICIO, no acredita hechos reales.',operacion=uuid4())

    def generar(self, **kwargs):
        kwargs.setdefault('operacion',uuid4())
        return generar_requerimiento(usuario=kwargs.pop('usuario',self.abogado),contrato_id=self.contrato.pk,tipo='gcof19',**kwargs)

    def contenido(self, generado):
        with generado.documento.archivo.open('rb') as f: return f.read()

    def root(self, contenido):
        with ZipFile(BytesIO(contenido)) as z:return E.fromstring(z.read('word/document.xml'))

    def texto(self, contenido):
        return '\n'.join(''.join(p.itertext()) for p in self.root(contenido).iter(W+'p'))

    def exportar(self, generado, nombre):
        if os.environ.get('SIGELC_MUESTRA_GCOF19'):
            (Path(os.environ['SIGELC_MUESTRA_GCOF19'])/nombre).write_bytes(self.contenido(generado))

    def test_multiples_generales_especificas_y_sin_cien_por_defecto(self):
        uno=self.obligacion()
        dos=self.obligacion(numero=1,tipo='especifica')
        tres=self.obligacion(numero=2)
        self.assertEqual(vigentes(self.contrato).count(),3)
        self.assertEqual({o.tipo for o in vigentes(self.contrato)},{'general','especifica'})
        self.assertTrue(all(o.porcentaje is None for o in (uno,dos,tres)))
        self.assertEqual(uno.estado_verificacion,'pendiente')

    def test_porcentaje_documentado_cero_parcial_y_cien_explicito(self):
        doc=self.soporte()
        for n,valor in enumerate((Decimal('0'),Decimal('62.50'),Decimal('100')),1):
            ob=self.obligacion(numero=n,porcentaje=valor,estado_verificacion='verificada',fundamento='Referencia FICTICIA página 1',documentos=[doc])
            self.assertEqual(ob.porcentaje,valor)
        g=self.generar()
        texto=self.texto(self.contenido(g))
        for valor in ('0,00 %','62,50 %','100,00 %'):self.assertIn(valor,texto)
        self.assertEqual(len(g.datos_utilizados['obligaciones']),3)

    def test_rechaza_porcentajes_fuera_rango_y_sin_sustento(self):
        doc=self.soporte()
        for porcentaje in (Decimal('-1'),Decimal('100.01')):
            with self.assertRaises(ValidationError):self.obligacion(porcentaje=porcentaje,estado_verificacion='verificada',fundamento='FICTICIO',documentos=[doc])
        for datos in [dict(porcentaje=10),dict(estado_verificacion='verificada'),
                      dict(porcentaje=10,estado_verificacion='verificada',fundamento='sin soporte')]:
            with self.assertRaises(ValidationError):self.obligacion(**datos)
        self.assertFalse(ObligacionContractual.objects.exists())

    def test_restricciones_base_porcentaje_y_duplicados(self):
        datos=dict(contrato=self.contrato,numero=1,tipo='general',descripcion='FICTICIO',responsable=self.abogado,version=1)
        for extras in [dict(porcentaje=Decimal('101'),estado_verificacion='verificada',fundamento='FICTICIO'),dict(porcentaje=1)]:
            with self.assertRaises(IntegrityError),transaction.atomic():
                models.QuerySet.bulk_create(ObligacionContractual.objects.all(),[ObligacionContractual(**datos,**extras,operacion=uuid4())])
        self.obligacion()
        with self.assertRaises(IntegrityError),transaction.atomic():
            models.QuerySet.bulk_create(ObligacionContractual.objects.all(),[ObligacionContractual(**datos,operacion=uuid4())])

    def test_soportes_hallazgos_propios_y_rechazo_ajenos(self):
        doc=self.soporte()
        obs=registrar_observacion(usuario=self.abogado,contrato_id=self.contrato.pk,descripcion='Hallazgo FICTICIO del abogado',
            tipo='otro',estado_revision='pendiente',evidencia='Referencia FICTICIA',documentos=[doc],operacion=uuid4())
        propia=self.obligacion(documentos=[doc],observaciones_abogado=[obs])
        self.assertEqual(propia.documentos.get(),doc)
        self.assertEqual(propia.observaciones_abogado.get(),obs)
        otro=type(self.contrato).objects.get(pk=self.contrato.pk);otro.pk=None;otro.numero_contrato='AJENO';otro.save()
        for datos in [dict(documentos=[doc]),dict(observaciones_abogado=[obs])]:
            with self.assertRaisesMessage(ValidationError,'mismo contrato'):self.obligacion(contrato_id=otro.pk,**datos)
        ajena=self.obligacion(contrato_id=otro.pk)
        with self.assertRaises(ValidationError):SoporteObligacion.objects.create(obligacion=ajena,documento=doc)
        with self.assertRaises(ValidationError):HallazgoObligacion.objects.create(obligacion=ajena,observacion=obs)

    def test_separa_comentario_no_verificado_y_hallazgo_abogado(self):
        doc=self.soporte()
        obs=registrar_observacion(usuario=self.abogado,contrato_id=self.contrato.pk,descripcion='HALLAZGO DEL ABOGADO FICTICIO',
            tipo='inconsistencia',estado_revision='observado',evidencia='Referencia',documentos=[doc],operacion=uuid4())
        ob=self.obligacion(comentario_supervisor='ANOTACION SIN ATRIBUCION',documentos=[doc],observaciones_abogado=[obs])
        g=self.generar();root=self.root(self.contenido(g))
        fila=next(r for r in root.iter(W+'tr') if 'Obligación FICTICIA A' in ''.join(r.itertext()))
        comentario=''.join(fila.findall(W+'tc')[4].itertext())
        self.assertNotIn('HALLAZGO',comentario);self.assertNotIn('SIN ATRIBUCION',comentario)
        self.assertIn('comentario verificado',comentario)
        texto=self.texto(self.contenido(g))
        self.assertIn('referencia a hallazgo del abogado',texto)
        self.assertIn('pendiente de confirmar su atribución',texto)
        self.assertEqual(g.datos_utilizados['obligaciones'][0]['id'],ob.pk)

    def test_comentario_verificado_y_atribucion_exige_soporte(self):
        with self.assertRaises(ValidationError):self.obligacion(comentario_verificado=True,comentario_supervisor='FICTICIO')
        doc=self.soporte()
        self.obligacion(comentario_verificado=True,comentario_supervisor='Comentario FICTICIO documentado',
            documentos=[doc],estado_verificacion='verificada',fundamento='Declaración FICTICIA del supervisor, página 1')
        texto=self.texto(self.contenido(self.generar()))
        self.assertIn('Comentario FICTICIO documentado',texto)

    def test_rectificaciones_historicas_y_obsoletas(self):
        uno=self.obligacion()
        dos=self.obligacion(anterior=uno,motivo='Corrección FICTICIA',actividad='Actividad FICTICIA nueva')
        self.assertEqual((dos.version,dos.anterior_id),(2,uno.pk))
        self.assertEqual(list(vigentes(self.contrato)),[dos])
        uno.refresh_from_db();self.assertEqual(uno.actividad,'')
        for datos in [dict(anterior=uno,motivo='Obsoleta'),dict(anterior=dos),dict(anterior=dos,motivo='x',numero=2)]:
            with self.assertRaises(ValidationError):self.obligacion(**datos)
        for operacion in [lambda:uno.save(),lambda:uno.delete(),lambda:ObligacionContractual.objects.filter(pk=uno.pk).update(actividad='alterada')]:
            with self.assertRaises(ValidationError):operacion()

    def test_reintentos_y_duplicados_obligacion(self):
        op=uuid4();uno=self.obligacion(operacion=op)
        self.assertEqual(self.obligacion(operacion=op).pk,uno.pk)
        self.assertEqual(Auditoria.objects.filter(accion='registrar_obligacion').count(),1)
        with self.assertRaisesMessage(ValidationError,'ya existe'):self.obligacion()

    def test_rollback_obligacion_y_referencias(self):
        doc=self.soporte();antes=Auditoria.objects.count()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):self.obligacion(documentos=[doc])
        self.assertFalse(ObligacionContractual.objects.exists());self.assertFalse(SoporteObligacion.objects.exists())
        self.assertEqual(Auditoria.objects.count(),antes)

    def test_generacion_datos_filas_grupos_y_muestra(self):
        doc=self.soporte()
        self.obligacion(documentos=[doc],actividad='Actividad FICTICIA registrada',producto='Producto FICTICIO parcial',
            porcentaje=Decimal('62.50'),estado_verificacion='verificada',fundamento='Soporte FICTICIO, página 1',
            comentario_supervisor='Comentario FICTICIO aportado y documentado',comentario_verificado=True)
        self.obligacion(numero=2)
        self.obligacion(tipo='especifica',descripcion='Obligación específica FICTICIA')
        g=self.generar();contenido=self.contenido(g);texto=self.texto(contenido)
        for value in (ROTULO,self.contrato.numero_contrato,self.contrato.supervisor_nombre,self.contrato.objeto_contractual,
                      'OBLIGACIONES GENERALES','OBLIGACIONES ESPECÍFICAS','62,50 %','Obligación específica FICTICIA'):
            self.assertIn(value,texto)
        for dato in ['ENSIN','1249','CLARA MERCEDES','CAROL ANDREA','100%']:self.assertNotIn(dato,texto)
        self.assertIn('dependencia',g.campos_pendientes);self.assertIn('certificaciones_supervisor',g.campos_pendientes)
        self.assertEqual(g.datos_utilizados['contrato_origen']['tipo_contractual'],'Prueba')
        root=self.root(contenido)
        tablas=[t for t in root.iter(W+'tbl') if 'OBLIGACIONES GENERALES' in ''.join(t.itertext()) or 'OBLIGACIONES ESPECÍFICAS' in ''.join(t.itertext())]
        self.assertEqual([len(t.findall(W+'tr')) for t in tablas],[4,3])
        self.exportar(g,'PRUEBA-FICTICIA-GCOF19-FUNCIONAL.docx')

    def test_muchas_obligaciones_sin_truncamiento(self):
        for numero in range(1,26):
            self.obligacion(numero=numero,descripcion=f'OBLIGACION-FICTICIA-{numero:02d} '+('Detalle FICTICIO de prueba, sin validez. '*12))
        g=self.generar();texto=self.texto(self.contenido(g))
        for numero in range(1,26):self.assertIn(f'OBLIGACION-FICTICIA-{numero:02d}',texto)
        self.assertEqual(len(g.datos_utilizados['obligaciones']),25)
        self.exportar(g,'PRUEBA-FICTICIA-GCOF19-25-OBLIGACIONES.docx')

    def test_sin_obligaciones_y_fechas_pendientes(self):
        self.contrato.fecha_inicio=None;self.contrato.fecha_terminacion=None;self.contrato.supervisor_nombre='';self.contrato.save()
        g=self.generar();texto=self.texto(self.contenido(g))
        self.assertIn('OBLIGACIONES PENDIENTES DE REGISTRO',texto)
        for campo in ('obligaciones','fecha_inicio','fecha_terminacion','supervisor'):self.assertIn(campo,g.campos_pendientes)
        self.assertEqual(g.datos_utilizados['obligaciones'],[])
        self.assertNotIn('100 %',texto)

    def test_conserva_paquete_estilos_encabezado_pie_celdas(self):
        self.obligacion();g=self.generar()
        with ZipFile(self.path) as a,ZipFile(BytesIO(self.contenido(g))) as b:
            for n in a.namelist():
                if n!='word/document.xml':self.assertEqual(a.read(n),b.read(n),n)
            ra,rb=[E.fromstring(z.read('word/document.xml')) for z in (a,b)]
        self.assertEqual(E.tostring(ra.find('.//'+W+'sectPr')),E.tostring(rb.find('.//'+W+'sectPr')))
        patron=next(t for t in ra.iter(W+'tbl') if '{{grupo_obligaciones}}' in ''.join(t.itertext()))
        salida=next(t for t in rb.iter(W+'tbl') if 'OBLIGACIONES GENERALES' in ''.join(t.itertext()))
        for tag in ('tblPr','tblGrid'):self.assertEqual(E.tostring(patron.find(W+tag)),E.tostring(salida.find(W+tag)))
        self.assertEqual(len(list(salida.iter(W+'tblHeader'))),2)
        cuerpo=rb.find(W+'body');elementos=list(cuerpo);indice=elementos.index(salida)
        self.assertEqual(elementos[indice-1].tag,W+'p')

    def test_versionado_instantaneas_revision_y_auditoria(self):
        ob=self.obligacion();op=uuid4();uno=self.generar(operacion=op)
        original=self.contenido(uno)
        self.assertEqual(self.generar(operacion=op).pk,uno.pk)
        self.obligacion(anterior=ob,motivo='Corrección FICTICIA',actividad='Actividad posterior FICTICIA')
        self.registrar_plantilla()
        dos=self.generar();self.assertEqual(dos.plantilla.version,2)
        self.assertNotEqual(uno.datos_utilizados['obligaciones'],dos.datos_utilizados['obligaciones'])
        revision=self.generar(anterior_id=dos.pk,archivo_revision=SimpleUploadedFile('editado.docx',self.contenido(dos)),motivo='Revisión FICTICIA')
        self.assertEqual(revision.version,3);self.assertEqual(revision.anterior_id,dos.pk)
        self.assertEqual(revision.datos_utilizados['obligaciones'],dos.datos_utilizados['obligaciones'])
        self.assertIn('revision_manual',revision.datos_utilizados)
        self.assertEqual(self.contenido(uno),original)
        self.assertEqual(Auditoria.objects.filter(entidad_afectada='DocumentoGenerado').count(),3)
        with self.assertRaises(ValidationError):self.generar(anterior_id=uno.pk,archivo_revision=SimpleUploadedFile('viejo.docx',original),motivo='Obsoleto')

    def test_revision_exige_motivo_y_rotulo(self):
        uno=self.generar()
        with self.assertRaisesMessage(ValidationError,'motivo'):
            self.generar(anterior_id=uno.pk,archivo_revision=SimpleUploadedFile('editado.docx',self.contenido(uno)))
        with self.assertRaisesMessage(ValidationError,'rótulo'):
            self.generar(anterior_id=uno.pk,archivo_revision=test_requerimientos.docx(),motivo='FICTICIO')
        self.assertEqual(DocumentoGenerado.objects.count(),1)

    def test_plantilla_alterada_no_aprobada_rechazada(self):
        with self.assertRaises(ValidationError):
            registrar_plantilla(usuario=self.abogado,tipo='gcof19',nombre='NO APROBADA',archivo=test_requerimientos.docx(),activar=True)

    def test_generacion_exige_identificador_reintento(self):
        with self.assertRaisesMessage(ValidationError,'identificador'):
            generar_requerimiento(usuario=self.abogado,contrato_id=self.contrato.pk,tipo='gcof19')

    def test_admin_obligaciones_previsualizacion_generacion(self):
        self.client.force_login(self.abogado)
        ficha=reverse('admin:gestion_contractual_contrato_change',args=[self.contrato.pk])
        self.assertContains(self.client.get(ficha),'GENERAR INFORME FINAL GCOF19')
        url=reverse('admin:gestion_contractual_obligaciones',args=[self.contrato.pk])
        pagina=self.client.get(url);self.assertContains(pagina,'obligaciones contractuales')
        self.assertEqual(self.client.post(url,{'numero':1,'tipo':'general','descripcion':'Registro FICTICIO desde Admin',
            'estado_verificacion':'pendiente','operacion':str(uuid4())}).status_code,302)
        ob=ObligacionContractual.objects.get()
        self.assertEqual(self.client.get(url+f'?rectificar={ob.pk}').context_data['form'].initial['anterior'],ob.pk)
        gen=reverse('admin:gestion_contractual_requerimientos',args=[self.contrato.pk])
        cantidad=DocumentoGenerado.objects.count()
        preview=self.client.post(gen,{'tipo':'gcof19','previsualizar':'1'})
        self.assertContains(preview,'Registro FICTICIO desde Admin');self.assertEqual(DocumentoGenerado.objects.count(),cantidad)
        self.assertEqual(self.client.post(gen,{'tipo':'gcof19','operacion':str(uuid4())}).status_code,302)
        self.assertContains(self.client.get(gen),'Instantánea histórica GCOF19')

    def test_permisos_no_operativo_y_sin_permiso(self):
        with self.assertRaises(PermissionDenied):self.obligacion(usuario=self.otro)
        with self.assertRaises(PermissionDenied):self.generar(usuario=self.otro)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(reverse('admin:gestion_contractual_obligaciones',args=[self.contrato.pk])).status_code,403)
        permiso=Permission.objects.get(content_type__app_label='gestion_contractual',codename='change_documentocontrato')
        self.abogado.user_permissions.remove(permiso)
        for key in ('_perm_cache','_user_perm_cache','_group_perm_cache'):self.abogado.__dict__.pop(key,None)
        with self.assertRaises(PermissionDenied):self.obligacion()

    def test_descarga_privada(self):
        g=self.generar();url=reverse('descargar_documento',args=[g.documento_id])
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.otro);self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.abogado);respuesta=self.client.get(url);self.assertEqual(respuesta.status_code,200)
        from django.core import signals
        from django.db import close_old_connections
        signals.request_finished.disconnect(close_old_connections)
        try:self.assertEqual(b''.join(respuesta.streaming_content),self.contenido(g))
        finally:signals.request_finished.connect(close_old_connections)

    def test_rollback_generacion_y_sin_estado(self):
        ob=self.obligacion();antes={p for p in self.media.rglob('*') if p.is_file()}
        cantidad=Auditoria.objects.count()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('Fallo FICTICIO')):
            with self.assertRaises(RuntimeError):self.generar()
        self.assertFalse(DocumentoGenerado.objects.exists())
        self.assertEqual({p for p in self.media.rglob('*') if p.is_file()},antes)
        self.assertEqual(Auditoria.objects.count(),cantidad)
        self.generar();self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual_id,self.estado.pk)
        self.assertFalse(self.contrato.historial_estados.exists());self.assertFalse(self.contrato.instancias.exists())

    def test_no_filtra_datos_de_otro_contrato(self):
        otro=type(self.contrato).objects.get(pk=self.contrato.pk);otro.pk=None;otro.numero_contrato='CONTRATO-AJENO';otro.save()
        self.obligacion(contrato_id=otro.pk,descripcion='DESCRIPCION-AJENA-NO-INCLUIR')
        g=self.generar()
        self.assertNotIn('DESCRIPCION-AJENA',self.texto(self.contenido(g)))
        self.assertEqual(g.datos_utilizados['obligaciones'],[])
