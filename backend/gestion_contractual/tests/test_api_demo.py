from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from unittest import skipUnless
from uuid import uuid4
from zipfile import ZipFile

from django.conf import settings
from django.core import signals
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from gestion_contractual.models import Auditoria, Contrato, DocumentoContrato, EstadoContrato, TipoRequerimiento
from gestion_contractual.requerimientos import registrar_plantilla
from gestion_contractual.services import cambiar_estado

from . import test_requerimientos
from .calendarios_ficticios import aprobar_ficticio


@skipUnless(getattr(settings, 'SIGELC_DEMO', False), 'Ejecute con la configuración aislada de la demo.')
class ApiDemoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        cls.secreto = uuid4().hex
        cls.abogado.set_password(cls.secreto)
        cls.abogado.save(update_fields=['password'])
        cls.otro.set_password(cls.secreto)
        cls.otro.save(update_fields=['password'])

    def setUp(self):
        test_requerimientos.RequerimientosTest.setUp(self)
        self.client = APIClient(enforce_csrf_checks=True)
        self.url = f'/api/contratos/{self.contrato.pk}/'

    def abrir_sesion(self):
        inicial = self.client.get('/api/sesion/').json()
        respuesta = self.client.post('/api/sesion/', {
            'username': self.abogado.username, 'password': self.secreto,
        }, format='json', HTTP_X_CSRFTOKEN=inicial['csrfToken'])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.csrf = respuesta.json()['csrfToken']
        return respuesta

    def escribir(self, metodo, ruta, datos, **headers):
        return getattr(self.client, metodo)(ruta, datos, format='json',
            HTTP_X_CSRFTOKEN=headers.pop('csrf', self.csrf), **headers)

    def contrato_nuevo(self):
        return {
            'numero_contrato': 'API-FICTICIO-01', 'anio_contractual': 2025,
            'objeto_contractual': 'Servicio ficticio para la demostración',
            'tipo_contrato': 'Servicios', 'modalidad_contratacion': 'Modalidad ficticia',
            'contratista_nombre': 'Firma de demostración', 'contratista_identificacion': 'FICTICIO-01',
            'valor_inicial': '85000.75', 'valor_actual': '85000.75', 'fecha_suscripcion': '2025-11-05',
            'fecha_inicio': '2025-11-06', 'fecha_terminacion': '2025-12-05',
            'fecha_asignacion': '2026-01-12', 'supervisor_nombre': 'Supervisión de demostración',
        }

    def iniciar(self, ruta=None):
        url = ruta or self.url
        return self.escribir('post', url + 'seguimiento/', {'confirmado': True})

    def plantilla(self, tipo=TipoRequerimiento.PRIMERO):
        raiz = Path(settings.BASE_DIR).parent / 'plantillas_institucionales'
        if tipo == TipoRequerimiento.PRIMERO:
            archivo = next((raiz / 'v1/preparadas').glob('01-*.docx'))
        else:
            archivo = next((raiz / 'v2').rglob(f'*{tipo}-v2.docx'))
        return registrar_plantilla(usuario=self.abogado, tipo=tipo, nombre='Institucional para pruebas',
            archivo=SimpleUploadedFile(archivo.name, archivo.read_bytes()), activar=True)

    def comprobar_descarga(self, url):
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('attachment', respuesta['Content-Disposition'])
        self.assertIn('no-store', respuesta['Cache-Control'])
        signals.request_finished.disconnect(close_old_connections)
        try:
            archivo = b''.join(respuesta.streaming_content)
        finally:
            signals.request_finished.connect(close_old_connections)
        return archivo

    def test_sesion_exige_csrf_incluso_anonima_y_cierra(self):
        credenciales = {'username': self.abogado.username, 'password': self.secreto}
        anonima = self.client.get('/api/sesion/')
        self.assertIsNone(anonima.json()['usuario'])
        self.assertIn('no-store', anonima['Cache-Control'])
        self.assertEqual(self.client.post('/api/sesion/', credenciales, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/sesion/', credenciales, format='json',
            HTTP_ORIGIN='http://example.invalid', HTTP_X_CSRFTOKEN=anonima.json()['csrfToken']).status_code, 403)
        sesion = self.abrir_sesion()
        self.assertEqual(sesion.json()['usuario']['username'], self.abogado.username)
        self.assertIn(settings.SESSION_COOKIE_NAME, sesion.cookies)
        self.assertTrue(sesion.cookies[settings.SESSION_COOKIE_NAME]['httponly'])
        self.assertEqual(self.client.delete('/api/sesion/').status_code, 403)
        logout = self.client.delete('/api/sesion/', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(logout.status_code, 200)
        self.assertIsNone(logout.json()['usuario'])
        self.assertEqual(self.client.get('/api/contratos/').status_code, 403)

    def test_credenciales_invalidas_y_usuario_no_operativo_no_inician_sesion(self):
        csrf = self.client.get('/api/sesion/').json()['csrfToken']
        for credenciales in (
            {'username': self.abogado.username, 'password': uuid4().hex},
            {'username': self.otro.username, 'password': self.secreto},
        ):
            respuesta = self.client.post('/api/sesion/', credenciales, format='json', HTTP_X_CSRFTOKEN=csrf)
            self.assertEqual(respuesta.status_code, 403)
            self.assertNotIn(settings.SESSION_COOKIE_NAME, respuesta.cookies)
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 403)

    def test_listado_busqueda_filtros_y_paginacion(self):
        self.abrir_sesion()
        nuevo = self.escribir('post', '/api/contratos/', self.contrato_nuevo())
        self.assertEqual(nuevo.status_code, 201, nuevo.content)
        self.assertEqual(self.client.get('/api/contratos/').json()['count'], 2)
        filtrados = self.client.get('/api/contratos/', {'buscar': 'firma de demostración', 'anio': '2025',
            'estado': TipoRequerimiento.PRIMERO})
        self.assertEqual(filtrados.json()['count'], 1)
        self.assertIsNone(filtrados.json()['next'])
        self.assertEqual(filtrados.json()['results'][0]['id'], nuevo.json()['id'])
        self.assertEqual(self.client.get('/api/contratos/', {'anio': 'otro'}).status_code, 400)

    def test_creacion_fija_entidad_estado_responsable_y_audita(self):
        self.abrir_sesion()
        datos = self.contrato_nuevo()
        respuesta = self.escribir('post', '/api/contratos/', datos)
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        salida = respuesta.json()
        contrato = Contrato.objects.get(pk=salida['id'])
        self.assertEqual(contrato.abogado_responsable_id, self.abogado.pk)
        self.assertEqual(contrato.entidad_id, self.contrato.entidad_id)
        self.assertEqual(salida['estado_actual']['codigo'], TipoRequerimiento.PRIMERO)
        self.assertEqual(salida['version_registro'], 1)
        self.assertEqual(salida['valor_actual'], datos['valor_actual'])
        self.assertTrue(salida['permisos']['iniciar_seguimiento'])
        self.assertTrue(Auditoria.objects.filter(accion='crear_contrato', identificador_registro=str(contrato.pk)).exists())

    def test_entrada_invalida_y_duplicados_rechazados(self):
        self.abrir_sesion()
        datos = self.contrato_nuevo()
        invalido = self.escribir('post', '/api/contratos/', {**datos, 'valor_actual': '-1.00'})
        self.assertEqual(invalido.status_code, 400)
        self.assertIn('valor_actual', invalido.json())
        incoherente = self.escribir('post', '/api/contratos/', {**datos, 'fecha_terminacion': '2025-10-01'})
        self.assertEqual(incoherente.status_code, 400)
        self.assertIn('fecha_terminacion', incoherente.json())
        self.assertEqual(self.escribir('post', '/api/contratos/', datos).status_code, 201)
        self.assertIn(self.escribir('post', '/api/contratos/', datos).status_code, (400, 409))

    def test_edicion_optimista_conserva_datos_y_auditoria(self):
        self.abrir_sesion()
        datos = {'version_registro': self.contrato.version_registro, 'observaciones': 'Edición ficticia'}
        respuesta = self.escribir('patch', self.url, datos)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()['version_registro'], 2)
        perdedor = self.escribir('patch', self.url, {**datos, 'observaciones': 'No debe sobrescribir'})
        self.assertEqual(perdedor.status_code, 409)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.observaciones, 'Edición ficticia')
        self.assertEqual(Auditoria.objects.filter(accion='editar_contrato', identificador_registro=str(self.contrato.pk)).count(), 1)
        self.assertEqual(self.escribir('patch', self.url, {'observaciones': 'Sin versión'}).status_code, 400)

    def test_rechaza_escrituras_de_estado_documentos_y_relaciones(self):
        self.abrir_sesion()
        for clave, valor in (('estado_actual', self.estado.pk), ('abogado_responsable', self.otro.pk),
                             ('documentos', []), ('entidad', self.contrato.entidad_id)):
            with self.subTest(clave=clave):
                respuesta = self.escribir('patch', self.url, {'version_registro': 1, clave: valor})
                self.assertEqual(respuesta.status_code, 400)
                self.assertIn(clave, respuesta.json())
        self.assertEqual(self.escribir('delete', self.url, {}).status_code, 405)
        self.assertEqual(self.escribir('post', self.url + 'documentos/', {}).status_code, 405)
        self.assertEqual(self.escribir('post', self.url + 'transiciones/', {}).status_code, 404)
        self.assertEqual(self.client.get('/admin/login/').status_code, 404)
        self.assertEqual(self.client.get('/documentos/1/descargar/').status_code, 404)

    def test_objetos_y_descargas_exigen_propiedad(self):
        self.abrir_sesion()
        ajeno = Contrato.objects.create(entidad=self.contrato.entidad, estado_actual=self.estado,
                                      abogado_responsable=self.otro, **self.contrato_nuevo())
        documento = DocumentoContrato.objects.create(contrato=ajeno, cargado_por=self.otro, tipo_documento='Soporte',
            nombre_original='prueba.pdf', archivo=SimpleUploadedFile('prueba.pdf', b'%PDF-1.4 ficticio'))
        self.assertEqual(self.client.get(f'/api/contratos/{ajeno.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/contratos/{ajeno.pk}/historial/').status_code, 404)
        self.assertEqual(self.escribir('patch', f'/api/contratos/{ajeno.pk}/', {
            'version_registro': 1, 'observaciones': 'No permitido',
        }).status_code, 404)
        self.assertEqual(self.client.get(f'/api/contratos/{ajeno.pk}/documentos/{documento.pk}/descargar/').status_code, 404)
        self.assertEqual(self.client.get(self.url + f'documentos/{documento.pk}/descargar/').status_code, 404)
        self.assertEqual(self.client.get('/api/contratos/').json()['count'], 1)

    def test_permiso_de_modelo_controla_edicion_y_documentos(self):
        from django.contrib.auth.models import Permission

        self.abrir_sesion()
        permisos = Permission.objects.filter(content_type__app_label='gestion_contractual',
            codename__in=['change_contrato', 'view_documentocontrato', 'view_historialestadocontrato'])
        self.abogado.user_permissions.remove(*permisos)
        detalle = self.client.get(self.url).json()
        self.assertFalse(detalle['permisos']['editar'])
        self.assertFalse(detalle['permisos']['ver_documentos'])
        self.assertEqual(self.escribir('patch', self.url, {'version_registro': 1, 'observaciones': 'No'}).status_code, 403)
        self.assertEqual(self.client.get(self.url + 'documentos/').status_code, 403)
        self.assertEqual(self.client.get(self.url + 'historial/').status_code, 403)
        self.assertEqual(self.client.get(self.url + 'primer-requerimiento/').status_code, 403)

    def test_seguimiento_pide_confirmacion_y_preserva_estado(self):
        self.abrir_sesion()
        contrato = self.escribir('post', '/api/contratos/', self.contrato_nuevo()).json()
        ruta = f"/api/contratos/{contrato['id']}/"
        self.assertEqual(self.escribir('post', ruta + 'seguimiento/', {'confirmado': False}).status_code, 400)
        seguimiento = self.iniciar(ruta)
        self.assertEqual(seguimiento.status_code, 201, seguimiento.content)
        self.assertEqual(seguimiento.json()['estado_actual']['codigo'], TipoRequerimiento.PRIMERO)
        vencimiento = seguimiento.json()['vencimiento_operativo']
        self.assertEqual(vencimiento['estado'], 'provisional')
        self.assertTrue(vencimiento['advertencia'])
        self.assertFalse(seguimiento.json()['puede_editar_fecha_asignacion'])
        self.assertEqual(self.client.get(ruta + 'vencimientos/').json(), seguimiento.json()['seguimiento'])
        self.assertEqual(self.iniciar(ruta).status_code, 400)

    def test_dashboard_cuenta_plazos_confirmados_y_advierte_provisionales(self):
        self.abrir_sesion()
        fecha = timezone.localdate() - timedelta(days=65)
        datos = self.contrato_nuevo()
        datos.update(fecha_asignacion=fecha.isoformat())
        contrato = self.escribir('post', '/api/contratos/', datos).json()
        ruta = f"/api/contratos/{contrato['id']}/"
        self.assertEqual(self.iniciar(ruta).status_code, 201)
        dashboard = self.client.get('/api/dashboard/').json()
        self.assertEqual((dashboard['total'], dashboard['activos'], dashboard['pendientes_liquidacion'], dashboard['vencidos']), (2, 1, 2, 0))
        self.assertEqual(len(dashboard['requieren_atencion']), 2)
        self.assertTrue(dashboard['definiciones']['vencidos'])
        aprobacion = aprobar_ficticio(self.abogado, fecha.year)
        instancia = Contrato.objects.get(pk=contrato['id']).instancias.get()
        if instancia.fecha_vencimiento.year != fecha.year:
            aprobacion = aprobar_ficticio(self.abogado, instancia.fecha_vencimiento.year)
        from gestion_contractual.calendarios_servicios import confirmar_vencimiento
        evaluacion = aprobacion.evaluaciones.get(instancia=instancia)
        confirmar_vencimiento(evaluacion_id=evaluacion.pk, usuario=self.abogado, motivo='Control ficticio',
                              confirmado=True, operacion=uuid4())
        self.assertEqual(self.client.get('/api/dashboard/').json()['vencidos'], 1)

    def test_historico_lee_evento_del_servicio_autorizado(self):
        self.abrir_sesion()
        contrato = self.escribir('post', '/api/contratos/', self.contrato_nuevo()).json()
        ruta = f"/api/contratos/{contrato['id']}/"
        seguimiento = self.iniciar(ruta).json()['seguimiento']
        destino = EstadoContrato.objects.get(codigo='revision-abogado-1')
        cambiar_estado(contrato_id=contrato['id'], estado_nuevo_id=destino.pk, usuario=self.abogado,
                        motivo='Transición consecutiva autorizada para prueba ficticia',
                        confirmado=True, instancia_esperada=seguimiento[0]['id'], fecha_efectiva=date(2026, 1, 20))
        detalle = self.client.get(ruta).json()
        historial = self.client.get(ruta + 'historial/').json()
        self.assertEqual(detalle['historial'], historial)
        self.assertEqual(historial[0]['estado_nuevo']['codigo'], 'revision-abogado-1')
        self.assertEqual(detalle['seguimiento'][0]['fecha_finalizacion'], '2026-01-20')

    def test_primer_requerimiento_institucional_versionado_y_descarga(self):
        self.abrir_sesion()
        self.plantilla()
        contrato = self.escribir('post', '/api/contratos/', self.contrato_nuevo()).json()
        ruta = f"/api/contratos/{contrato['id']}/"
        generacion = ruta + 'primer-requerimiento/'
        self.assertFalse(self.client.get(generacion).json()['disponible'])
        self.assertEqual(self.iniciar(ruta).status_code, 201)
        previo = self.client.get(generacion).json()
        self.assertTrue(previo['disponible'], previo)
        self.assertTrue(any(campo['pendiente'] for campo in previo['campos']))
        operacion = str(uuid4())
        solicitud = self.escribir('post', generacion, {'operacion': operacion})
        self.assertEqual(solicitud.status_code, 201, solicitud.content)
        borrador = solicitud.json()
        self.assertEqual(borrador['version'], 1)
        self.assertTrue(borrador['campos_pendientes'])
        self.assertEqual(self.escribir('post', generacion, {'operacion': operacion}).json()['id'], borrador['id'])
        self.assertEqual(self.escribir('post', generacion, {'operacion': str(uuid4())}).json()['version'], 2)
        descarga = borrador['documento']['descarga_url']
        with ZipFile(BytesIO(self.comprobar_descarga(descarga))) as paquete:
            self.assertIsNone(paquete.testzip())
            principal = paquete.read('word/document.xml')
            self.assertIn(b'API-FICTICIO-01', principal)
            self.assertIn(b'PROVISIONAL', principal)
        self.assertIn(borrador['documento'], self.client.get(ruta + 'documentos/').json())
        self.assertEqual(len(self.client.get(generacion).json()['versiones']), 2)
        self.assertTrue(Auditoria.objects.filter(accion='descargar_documento',
            identificador_registro=str(borrador['documento']['id'])).exists())
        self.client.delete('/api/sesion/', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(self.client.get(descarga).status_code, 403)

    def test_sucesivos_bloqueados_en_etapa_incorrecta(self):
        self.abrir_sesion()
        for tipo in (TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO):
            ruta = self.url + tipo + '/'
            self.assertFalse(self.client.get(ruta).json()['disponible'])
            self.assertEqual(self.escribir('post', ruta, {'operacion': str(uuid4())}).status_code, 400)
        self.assertFalse(self.contrato.borradores_requerimiento.exists())

    def test_sucesivos_generan_en_etapas_consecutivas_autorizadas(self):
        self.abrir_sesion()
        self.plantilla(TipoRequerimiento.SEGUNDO)
        self.plantilla(TipoRequerimiento.TERCERO)
        contrato = self.escribir('post', '/api/contratos/', self.contrato_nuevo()).json()
        ruta = f"/api/contratos/{contrato['id']}/"
        etapa = self.iniciar(ruta)
        self.assertEqual(etapa.status_code, 201)
        actual = etapa.json()['seguimiento'][0]['id']
        avance = (
            'revision-abogado-1', 'revision-supervisor-1', TipoRequerimiento.SEGUNDO,
            'revision-abogado-2', 'revision-supervisor-2', TipoRequerimiento.TERCERO,
        )
        for codigo in avance:
            destino = EstadoContrato.objects.get(codigo=codigo)
            cambiar_estado(contrato_id=contrato['id'], estado_nuevo_id=destino.pk, usuario=self.abogado,
                            motivo='Hito consecutivo ficticio', confirmado=True,
                            instancia_esperada=actual, fecha_efectiva=date(2026, 1, 20))
            etapa = Contrato.objects.get(pk=contrato['id']).instancias.get(fecha_finalizacion__isnull=True)
            actual = etapa.pk
            if codigo in (TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO):
                generacion = ruta + codigo + '/'
                self.assertTrue(self.client.get(generacion).json()['disponible'])
                respuesta = self.escribir('post', generacion, {'operacion': str(uuid4())})
                self.assertEqual(respuesta.status_code, 201, respuesta.content)
                self.assertEqual(respuesta.json()['version'], 1)
                contenido = self.comprobar_descarga(respuesta.json()['documento']['descarga_url'])
                with ZipFile(BytesIO(contenido)) as paquete:
                    self.assertIsNone(paquete.testzip())

    def test_edicion_sin_csrf_cancelada(self):
        self.abrir_sesion()
        respuesta = self.client.patch(self.url, {'version_registro': 1, 'observaciones': 'No'}, format='json')
        self.assertEqual(respuesta.status_code, 403)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.version_registro, 1)
