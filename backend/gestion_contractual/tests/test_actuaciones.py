from datetime import timedelta
from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ..actuaciones_models import ActuacionContractual, EventoActuacionContractual
from ..models import Auditoria, Contrato, DocumentoContrato, EntidadPublica, PerfilUsuario
from . import test_api_demo, test_requerimientos


@skipUnless(getattr(settings, 'SIGELC_DEMO', False), 'Ejecute con la configuración aislada de la demo.')
class ActuacionesAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Los dos indicadores únicos se liberan exclusivamente dentro de la
        # transacción de TestCase, incluso si la demo ya tiene expedientes.
        EntidadPublica.objects.filter(activo=True).update(activo=False)
        PerfilUsuario.objects.filter(abogado_operativo=True).update(abogado_operativo=False)
        test_api_demo.ApiDemoTest.setUpTestData.__func__(cls)

    def setUp(self):
        test_api_demo.ApiDemoTest.setUp(self)
        self.actuaciones = self.url + 'actuaciones/'

    abrir_sesion = test_api_demo.ApiDemoTest.abrir_sesion
    escribir = test_api_demo.ApiDemoTest.escribir
    contrato_nuevo = test_api_demo.ApiDemoTest.contrato_nuevo

    def _plan(self, **datos):
        return {
            'accion': 'planificar', 'operacion': str(uuid4()), 'titulo': 'Revisar soportes ficticios',
            'descripcion': 'Verificar el informe recibido.',
            'vence': (timezone.localdate() + timedelta(days=4)).isoformat(),
            **datos,
        }

    def _evento(self, actuacion_id, accion='realizar', **datos):
        return {
            'accion': accion, 'operacion': str(uuid4()), 'actuacion_id': actuacion_id,
            'fecha': timezone.localdate().isoformat(), 'descripcion': 'Se revisó el informe con respaldo.',
            **datos,
        }

    def _post(self, datos):
        return self.escribir('post', self.actuaciones, datos)

    def _registrar(self, datos):
        resultado = self._post(datos)
        self.assertEqual(resultado.status_code, 201, resultado.content)
        return resultado.json()['actuaciones'][0]

    def test_tarea_conserva_plan_y_registra_accion_auditada(self):
        self.abrir_sesion()
        plan = self._plan()
        tarea = self._registrar(plan)
        self.assertEqual((tarea['estado'], tarea['eventos'], tarea['vence']), ('pendiente', [], plan['vence']))
        terminada = self._registrar(self._evento(tarea['id']))
        self.assertEqual(terminada['estado'], 'realizada')
        self.assertEqual(terminada['descripcion'], plan['descripcion'])
        self.assertEqual(terminada['eventos'][0]['accion'], 'realizar')
        self.assertEqual(terminada['eventos'][0]['usuario'], self.abogado.username)
        self.assertEqual(self.client.get(self.actuaciones).json()['actuaciones'], [terminada])
        self.assertEqual(Auditoria.objects.filter(
            usuario=self.abogado, accion__in=['actuacion_planificar', 'actuacion_realizar'],
        ).count(), 2)
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual_id, self.estado.pk)
        self.assertFalse(self.contrato.historial_estados.exists())

    def test_cancelacion_conserva_historial_e_impide_otro_cierre(self):
        self.abrir_sesion()
        tarea = self._registrar(self._plan())
        cancelacion = self._evento(tarea['id'], 'cancelar', descripcion='Se reorganizó el trabajo.')
        cancelada = self._registrar(cancelacion)
        self.assertEqual(cancelada['estado'], 'cancelada')
        self.assertEqual(cancelada['eventos'][0]['descripcion'], cancelacion['descripcion'])
        self.assertEqual(self._post(self._evento(tarea['id'])).status_code, 409)
        self.assertEqual(EventoActuacionContractual.objects.filter(actuacion_id=tarea['id']).count(), 1)

    def test_radicado_vincula_soporte_del_mismo_expediente(self):
        self.abrir_sesion()
        soporte = DocumentoContrato.objects.create(
            contrato=self.contrato, cargado_por=self.abogado, tipo_documento='Soporte',
            nombre_original='FICTICIA.docx', archivo=test_requerimientos.docx(),
        )
        memo = {
            'accion': 'memorando', 'operacion': str(uuid4()), 'titulo': 'Acuse de recepción',
            'radicado': 'FICTICIO-RAD-01', 'fecha': timezone.localdate().isoformat(), 'documento_id': soporte.pk,
        }
        salida = self._registrar(memo)
        self.assertEqual((salida['radicado'], salida['documento_id'], salida['estado']),
                         ('FICTICIO-RAD-01', soporte.pk, 'registrado'))
        self.assertEqual(self._post(self._evento(salida['id'])).status_code, 404)
        distinto = Contrato.objects.create(
            entidad=self.contrato.entidad, estado_actual=self.estado, abogado_responsable=self.abogado,
            **self.contrato_nuevo(),
        )
        self.assertEqual(self.escribir('post', f'/api/contratos/{distinto.pk}/actuaciones/',
                                      self._plan(documento_id=soporte.pk)).status_code, 400)

    def test_reintento_no_duplica_y_reuso_distinto_conflicto(self):
        self.abrir_sesion()
        plan = self._plan()
        original = self._registrar(plan)
        self.assertEqual(self._registrar(plan)['id'], original['id'])
        self.assertEqual(self._post({**plan, 'titulo': 'No es el mismo trabajo'}).status_code, 409)
        cierre = self._evento(original['id'])
        una = self._registrar(cierre)
        otra = self._registrar(cierre)
        self.assertEqual(una, otra)
        self.assertEqual(self._post({**cierre, 'operacion': plan['operacion']}).status_code, 409)
        self.assertEqual(self.contrato.actuaciones.count(), 1)
        self.assertEqual(Auditoria.objects.filter(usuario=self.abogado, accion='actuacion_realizar').count(), 1)

    def test_sesion_csrf_propiedad_y_permiso_de_escritura(self):
        self.assertEqual(self.client.get(self.actuaciones).status_code, 403)
        self.abrir_sesion()
        self.assertEqual(self.client.post(self.actuaciones, self._plan(), format='json').status_code, 403)
        ajeno = Contrato.objects.create(entidad=self.contrato.entidad, estado_actual=self.estado,
                                       abogado_responsable=self.otro, **self.contrato_nuevo())
        ruta = f'/api/contratos/{ajeno.pk}/actuaciones/'
        self.assertEqual(self.client.get(ruta).status_code, 404)
        self.assertEqual(self.escribir('post', ruta, self._plan()).status_code, 404)
        cambio = Permission.objects.get(content_type__app_label='gestion_contractual', codename='change_contrato')
        self.abogado.user_permissions.remove(cambio)
        self.assertFalse(self.client.get(self.actuaciones).json()['puede_registrar'])
        self.assertEqual(self._post(self._plan()).status_code, 403)

    def test_rechaza_fecha_futura_y_campos_de_estado_inyectados(self):
        self.abrir_sesion()
        self.assertEqual(self._post(self._plan(estado_actual='liquidado')).status_code, 400)
        tarea = self._registrar(self._plan())
        futura = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self._post(self._evento(tarea['id'], fecha=futura)).status_code, 400)
        self.assertEqual(self.client.get(self.actuaciones).json()['actuaciones'][0]['estado'], 'pendiente')

    def test_modelos_no_permiten_reescribir_contenido_o_evento(self):
        self.abrir_sesion()
        tarea = self._registrar(self._plan())
        self._registrar(self._evento(tarea['id']))
        registro = ActuacionContractual.objects.get(pk=tarea['id'])
        registro.titulo = 'Copia adulterada'
        with self.assertRaises(ValidationError):
            registro.save()
        with self.assertRaises(ValidationError):
            registro.eventos.update(descripcion='Copia adulterada')
        with self.assertRaises(ValidationError):
            registro.delete()

    def test_dashboard_muestra_tarea_abierta_y_la_retira_al_realizarla(self):
        self.abrir_sesion()
        tarea = self._registrar(self._plan())
        panel = self.client.get('/api/dashboard/').json()
        self.assertEqual(panel['tareas_pendientes'], [{
            'id': tarea['id'], 'contrato_id': self.contrato.pk,
            'numero_contrato': self.contrato.numero_contrato,
            'titulo': tarea['titulo'], 'vence': tarea['vence'],
        }])
        self.assertEqual(panel['tareas_vencidas'], 0)
        self._registrar(self._evento(tarea['id']))
        self.assertEqual(self.client.get('/api/dashboard/').json()['tareas_pendientes'], [])
