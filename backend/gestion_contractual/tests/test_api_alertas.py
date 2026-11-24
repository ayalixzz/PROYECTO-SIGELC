from datetime import date, timedelta
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase

from gestion_contractual.competencia import registrar_calculo
from gestion_contractual.models import Auditoria, Contrato, RegistroAlertaContrato
from gestion_contractual.services import iniciar_seguimiento

from . import test_api_demo
from .calendarios_ficticios import aprobar_ficticio


LIMITE = date(2027, 8, 12)


@skipUnless(getattr(settings, 'SIGELC_DEMO', False), 'Ejecute con la configuración aislada de la demo.')
class AlertasAPITest(TestCase):
    setUpTestData = classmethod(test_api_demo.ApiDemoTest.setUpTestData.__func__)
    abrir_sesion = test_api_demo.ApiDemoTest.abrir_sesion
    escribir = test_api_demo.ApiDemoTest.escribir
    contrato_nuevo = test_api_demo.ApiDemoTest.contrato_nuevo

    def setUp(self):
        test_api_demo.ApiDemoTest.setUp(self)
        self.ruta = self.url + 'alertas/'
        self.abrir_sesion()

    def ficha(self):
        respuesta = self.client.get(self.ruta)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()

    def registrar(self, ruta=None):
        respuesta = self.escribir('post', ruta or self.ruta + 'registrar/', {})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()['registradas']

    def proponer(self, **insumos):
        for anio in (2025, 2027):
            aprobar_ficticio(self.abogado, anio)
        self.contrato.fecha_terminacion = date(2025, 2, 12)
        self.contrato.save()
        return registrar_calculo(
            contrato_id=self.contrato.pk, usuario=self.abogado, operacion=uuid4(), anterior_id=None,
            datos={'sujeto': 'si', 'regimen': 'art11_cpaca', 'origen': 'ejecucion', 'pacto': 'no',
                   'liquidacion': 'ninguna', 'incidencias': 'no', **insumos},
        )

    def verificar(self, propuesta):
        return registrar_calculo(
            contrato_id=self.contrato.pk, usuario=self.abogado, operacion=uuid4(), anterior_id=propuesta.pk,
            accion='verificar', fundamento='Fundamento ficticio de prueba aislada.',
        )

    def test_propuesta_no_publica_alerta_juridica_y_verificacion_si(self):
        with patch('django.utils.timezone.localdate', return_value=LIMITE - timedelta(days=7)):
            sin_calculo = self.ficha()
            self.assertEqual(sin_calculo['alertas'], [])
            self.assertEqual(sin_calculo['revisiones'][0]['tipo'], 'competencia')
            propuesta = self.proponer()
            self.assertEqual(self.ficha()['alertas'], [])
            self.assertEqual(self.registrar(), 0)
            fuente = self.verificar(propuesta)
            ficha = self.ficha()
            self.assertEqual(ficha['revisiones'], [])
            aviso, = ficha['alertas']
            self.assertEqual(
                (aviso['tipo'], aviso['hito'], aviso['dias_restantes'], aviso['prioridad']),
                ('competencia', '7', 7, 'alta'),
            )
            self.assertEqual(aviso['fecha_objetivo'], LIMITE.isoformat())
            self.assertEqual(aviso['fuente'], {'tipo': 'calculo_competencia', 'id': fuente.pk, 'version': fuente.version})
            panel = self.client.get('/api/dashboard/').json()['alertas']
            self.assertEqual(panel['totales'], {'juridicas': 1, 'operativas': 0, 'requieren_revision': 0})
            self.assertEqual(panel['juridicas'], [aviso])
            self.assertFalse(RegistroAlertaContrato.objects.exists())
        with patch('django.utils.timezone.localdate', return_value=LIMITE - timedelta(days=40)):
            ficha = self.ficha()
            self.assertEqual(ficha['alertas'], [])
            self.assertEqual(ficha['fecha_competencia'], {'fecha': LIMITE.isoformat(), 'fuente': aviso['fuente']})

    def test_alerta_registrada_se_conserva_tras_dejar_de_ser_vigente(self):
        calculo = self.verificar(self.proponer())
        with patch('django.utils.timezone.localdate', return_value=LIMITE):
            self.assertEqual(self.registrar('/api/alertas/registrar/'), 1)
            self.assertEqual(self.ficha()['historial'][0]['activa'], True)
            self.assertEqual(self.registrar(), 0)
            aprobar_ficticio(self.abogado, 2027, [{
                'fecha': LIMITE.isoformat(), 'habil': False,
                'motivo': 'Excepción ficticia', 'fuente': 'https://example.invalid/ACTO-FICTICIO',
            }])
            ficha = self.ficha()
            self.assertEqual(ficha['alertas'], [])
            self.assertEqual(ficha['revisiones'][0]['tipo'], 'competencia')
            anterior, = ficha['historial']
            self.assertEqual((anterior['activa'], anterior['fuente']['id']), (False, calculo.pk))
            self.assertEqual(self.registrar(), 0)
        self.assertEqual(RegistroAlertaContrato.objects.count(), 1)

    def test_cruzar_otra_ventana_registra_otro_hito_y_conserva_el_original(self):
        self.verificar(self.proponer())
        for dias, hito in [(8, '15'), (1, '1'), (0, 'hoy'), (-3, 'vencida')]:
            with self.subTest(hito=hito), patch('django.utils.timezone.localdate', return_value=LIMITE - timedelta(days=dias)):
                self.assertEqual(self.registrar(), 1)
                self.assertEqual(self.registrar(), 0)
                ficha = self.ficha()
                self.assertEqual(ficha['alertas'][0]['hito'], hito)
                self.assertEqual([a['hito'] for a in ficha['historial'] if a['activa']], [hito])
        self.assertEqual(RegistroAlertaContrato.objects.count(), 4)
        registro = RegistroAlertaContrato.objects.first()
        with self.assertRaises(ValidationError):
            registro.save()
        with self.assertRaises(ValidationError):
            registro.delete()
        with self.assertRaises(ValidationError):
            RegistroAlertaContrato.objects.all().update(instantanea={})
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual, self.estado)
        self.assertFalse(self.contrato.historial_estados.exists())

    def test_liquidacion_previa_verificada_no_necesita_fecha_de_competencia(self):
        self.verificar(self.proponer(liquidacion='bilateral', fecha_liquidacion='2025-06-09', firma_acta='2025-06-10'))
        ficha = self.ficha()
        self.assertEqual(ficha['alertas'], [])
        self.assertEqual(ficha['revisiones'], [])

    def test_vencimiento_operativo_cuenta_dias_habiles_y_revisa_cambios(self):
        aprobar_ficticio(self.abogado, 2026)
        self.contrato.fecha_asignacion = date(2026, 1, 12)
        self.contrato.save()
        with patch('django.utils.timezone.localdate', return_value=date(2026, 1, 30)):
            instancia = iniciar_seguimiento(
                contrato_id=self.contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True,
            )
            aviso, = self.ficha()['alertas']
            self.assertEqual((aviso['tipo'], aviso['fecha_objetivo'], aviso['dias_restantes'], aviso['unidad']),
                             ('operativa', '2026-02-02', 1, 'habiles'))
            self.assertEqual(aviso['fuente'], {'tipo': 'instancia_seguimiento', 'id': instancia.pk})
            aprobar_ficticio(self.abogado, 2026, [{
                'fecha': '2026-01-13', 'habil': False, 'motivo': 'Excepción ficticia',
                'fuente': 'https://example.invalid/ACTO-FICTICIO',
            }])
            ficha = self.ficha()
            self.assertEqual(ficha['alertas'], [])
            self.assertEqual({r['tipo'] for r in ficha['revisiones']}, {'competencia', 'operativa'})
            instancia.refresh_from_db()
            self.assertEqual(instancia.fecha_vencimiento, date(2026, 2, 2))

    def test_rutas_exigen_propiedad_csrf_y_permiso_de_escritura(self):
        ajeno = Contrato.objects.create(
            entidad=self.contrato.entidad, estado_actual=self.estado,
            abogado_responsable=self.otro, **self.contrato_nuevo(),
        )
        ajena = f'/api/contratos/{ajeno.pk}/alertas/'
        self.assertEqual(self.client.get(ajena).status_code, 404)
        self.assertEqual(self.escribir('post', ajena + 'registrar/', {}).status_code, 404)
        self.assertEqual(self.client.post(self.ruta + 'registrar/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/alertas/registrar/', {}, format='json').status_code, 403)
        self.assertEqual(self.escribir('post', self.ruta + 'registrar/', {'fecha': LIMITE.isoformat()}).status_code, 400)
        permiso = Permission.objects.get(content_type__app_label='gestion_contractual', codename='change_contrato')
        self.abogado.user_permissions.remove(permiso)
        self.assertFalse(self.ficha()['puede_registrar'])
        self.assertFalse(self.client.get('/api/dashboard/').json()['puede_registrar_alertas'])
        self.assertEqual(self.escribir('post', self.ruta + 'registrar/', {}).status_code, 403)
        self.assertEqual(self.escribir('post', self.ruta + 'prioridad/', {}).status_code, 403)

    def test_resumen_del_ultimo_memorando_conserva_todas_las_actuaciones(self):
        ruta = self.url + 'actuaciones/'
        self.assertIsNone(self.ficha()['ultimo_memorando'])
        with patch('django.utils.timezone.localdate', return_value=date(2026, 3, 1)):
            for radicado, fecha in [('R-1', '2026-02-25'), ('R-2', '2026-02-20'), ('R-3', '2026-02-25')]:
                respuesta = self.escribir('post', ruta, {
                    'accion': 'memorando', 'operacion': str(uuid4()),
                    'titulo': 'Memorando ficticio ' + radicado, 'radicado': radicado, 'fecha': fecha,
                })
                self.assertEqual(respuesta.status_code, 201, respuesta.content)
            respuesta = self.escribir('post', ruta, {
                'accion': 'planificar', 'operacion': str(uuid4()), 'titulo': 'Tarea ficticia más reciente',
            })
            self.assertEqual(respuesta.status_code, 201, respuesta.content)
        actuaciones = self.client.get(ruta).json()
        esperado = actuaciones['ultimo_memorando']
        self.assertEqual(esperado['radicado'], 'R-3')
        self.assertEqual(esperado['fecha'], '2026-02-25')
        self.assertEqual(self.ficha()['ultimo_memorando'], esperado)
        self.assertEqual(len(actuaciones['actuaciones']), 4)

    def test_prioridad_manual_expira_con_auditoria_y_respeta_critica(self):
        self.verificar(self.proponer())
        datos = {'operacion': str(uuid4()), 'motivo': 'Revisar expediente de prueba', 'vence': LIMITE.isoformat()}
        ruta = self.ruta + 'prioridad/'
        with patch('django.utils.timezone.localdate', return_value=LIMITE - timedelta(days=3)):
            anticipada = {**datos, 'vence': (LIMITE - timedelta(days=4)).isoformat()}
            self.assertEqual(self.escribir('post', ruta, anticipada).status_code, 400)
            primera = self.escribir('post', ruta, datos)
            self.assertEqual(primera.status_code, 200, primera.content)
            reintento = self.escribir('post', ruta, datos)
            self.assertEqual(reintento.status_code, 200, reintento.content)
            self.assertEqual(self.escribir('post', ruta, {**datos, 'motivo': 'Otro motivo'}).status_code, 400)
            self.assertEqual(primera.json()['prioridad']['origen'], 'manual')
            panel = self.client.get('/api/dashboard/').json()['alertas']
            temporal, = panel['prioridades_temporales']
            self.assertEqual((temporal['contrato_id'], temporal['motivo'], temporal['vence']),
                             (self.contrato.pk, datos['motivo'], datos['vence']))
        with patch('django.utils.timezone.localdate', return_value=LIMITE - timedelta(days=1)):
            prioridad = self.ficha()['prioridad']
            self.assertEqual((prioridad['nivel'], prioridad['origen']), ('critica', 'competencia'))
        with patch('django.utils.timezone.localdate', return_value=LIMITE + timedelta(days=1)):
            previa = self.contrato.calculos_competencia.first()
            registrar_calculo(
                contrato_id=self.contrato.pk, usuario=self.abogado, operacion=uuid4(),
                anterior_id=previa.pk, datos={**previa.insumos, 'incidencias': 'si'},
            )
            self.assertEqual(self.ficha()['prioridad']['nivel'], 'normal')
        self.assertEqual(Auditoria.objects.filter(accion='registrar_prioridad_temporal').count(), 1)
        self.assertEqual(self.contrato.prioridades_temporales.count(), 1)
