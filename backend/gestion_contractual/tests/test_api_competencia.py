from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.management.base import CommandError
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from gestion_contractual.alertas_politica import evaluar_alertas
from gestion_contractual.api_competencia import CompetenciaView, EntradaCompetencia, _ficha
from gestion_contractual.calendario import DiasCalendario, calendario_nacional
from gestion_contractual.calendarios_servicios import evaluar_dependencias
from gestion_contractual.competencia import Estado, FECHAS_RESULTADO, datos_contrato, evaluar
from gestion_contractual.management.commands.sembrar_demo import Command, plan_competencia_demo


MODULO = 'gestion_contractual.api_competencia'


class CompetenciaAPITest(SimpleTestCase):
    def setUp(self):
        self.contrato = SimpleNamespace(
            pk=4, fecha_terminacion=date(2026, 5, 10), tipo_contrato='Obra', modalidad_contratacion='Licitación',
        )
        self.usuario = Mock()
        self.usuario.has_perms.side_effect = lambda permisos: 'gestion_contractual.view_documentocontrato' not in permisos
        self.datos = {
            'operacion': str(uuid4()), 'anterior_id': None, 'accion': 'calcular',
            'datos': {'fecha_terminacion': '2026-05-10', 'sujeto': 'si', 'regimen': 'art11_cpaca',
                      'origen': 'ejecucion', 'pacto': 'no', 'liquidacion': 'ninguna', 'incidencias': 'no'},
        }

    def test_ejecuta_propuesta_con_servicio_existente_y_fechas_iso(self):
        request = SimpleNamespace(user=self.usuario, data=self.datos)
        with patch(f'{MODULO}.expediente', return_value=self.contrato), \
                patch(f'{MODULO}._ficha', return_value={'actual': {'estado': 'pendiente'}}), \
                patch(f'{MODULO}.registrar_calculo') as registrar:
            respuesta = CompetenciaView.post.__wrapped__(CompetenciaView(), request, self.contrato.pk)
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(registrar.call_args.kwargs['datos']['fecha_terminacion'], '2026-05-10')
        self.assertEqual(registrar.call_args.kwargs['accion'], 'calcular')
        self.assertNotIn('fundamento', registrar.call_args.kwargs)

    def test_rechaza_intento_de_escribir_una_fecha_confirmada_o_regla(self):
        raw = {**self.datos, 'datos': {**self.datos['datos'], 'limite_competencia': '2026-05-10'}}
        serializador = EntradaCompetencia(data=raw)
        self.assertFalse(serializador.is_valid())
        self.assertIn('limite_competencia', serializador.errors['datos'])

    def test_verificar_requiere_confirmacion_booleana_fundamento_y_version(self):
        base = {'operacion': str(uuid4()), 'anterior_id': 22, 'accion': 'verificar', 'fundamento': 'Revisado con soporte.'}
        for faltante in (
            {}, {'confirmar': False}, {'confirmar': 'true'}, {'confirmar': True, 'fundamento': ''},
            {'confirmar': True, 'anterior_id': None}, {'confirmar': True, 'datos': {}},
        ):
            with self.subTest(discrepancia=faltante):
                self.assertFalse(EntradaCompetencia(data={**base, **faltante}).is_valid())
        self.assertTrue(EntradaCompetencia(data={**base, 'confirmar': True}).is_valid())

    def test_rectificacion_completa_solo_registra_propuesta(self):
        fechas = dict.fromkeys(FECHAS_RESULTADO)
        fechas.update(fecha_base='2026-05-10', limite_competencia='2028-11-10')
        datos = {
            'operacion': str(uuid4()), 'anterior_id': 22, 'accion': 'rectificar', 'rectificacion': fechas,
            'fundamento': 'Fuente verificada por el abogado.', 'motivo': 'Corregir fecha de referencia.', 'confirmar': True,
        }
        request = SimpleNamespace(user=self.usuario, data=datos)
        with patch(f'{MODULO}.expediente', return_value=self.contrato), \
                patch(f'{MODULO}._ficha', return_value={'actual': {'estado': 'pendiente'}}), \
                patch(f'{MODULO}.registrar_calculo') as registrar:
            respuesta = CompetenciaView.post.__wrapped__(CompetenciaView(), request, self.contrato.pk)
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(registrar.call_args.kwargs['accion'], 'rectificar')
        self.assertEqual(registrar.call_args.kwargs['rectificacion'], fechas)
        self.assertNotIn('confirmar', registrar.call_args.kwargs)

    def test_lectura_de_corte_no_publica_calculo_obsoleto_o_provisional(self):
        contrato = self.contrato
        limite = '2028-11-10'
        registro = SimpleNamespace(
            pk=7, version=1, estado=Estado.VERIFICADO, calculado_en=None, verificado_en=None,
            verificado_por=None,
            insumos=datos_contrato(contrato), resultado={
                'limite_competencia': limite, 'dependencias_calendario': {'fechas': [{'anio': 2028}]},
            },
            motivo_rectificacion='', fundamento_verificacion='Fuente verificada.',
            get_estado_display=lambda: 'Verificado',
        )
        contrato.calculos_competencia = Mock()
        contrato.calculos_competencia.select_related.return_value = [registro]
        with patch(f'{MODULO}.CalendarioVerificado.objects.filter') as anios, \
                patch(f'{MODULO}.evaluar_vigencia') as vigencia, \
                patch(f'{MODULO}.calculo_verificado_vigente') as confirmado:
            anios.return_value.values_list.return_value = [2026, 2028]
            vigencia.return_value = {'clasificacion': 'sin_impacto', 'motivo': ''}
            confirmado.return_value = registro
            self.assertEqual(_ficha(contrato, self.usuario)['fecha_confirmada'], limite)
            vigencia.return_value = {'clasificacion': 'incierto', 'motivo': 'No hay cobertura validada.'}
            confirmado.return_value = None
            anios.return_value.values_list.return_value = [2026]
            resultado = _ficha(contrato, self.usuario)
            self.assertIsNone(resultado['fecha_confirmada'])
            self.assertEqual(resultado['actual']['fechas']['limite_competencia'], limite)
            self.assertEqual(resultado['anios_pendientes'], [2028])
            self.assertIn('cobertura', resultado['motivo_revision'])
            registro.estado = Estado.PENDIENTE
            vigencia.return_value = {'clasificacion': 'sin_impacto', 'motivo': ''}
            resultado = _ficha(contrato, self.usuario)
            self.assertTrue(resultado['actual']['puede_verificar'])
            self.assertIsNone(resultado['fecha_confirmada'])

    def test_rechaza_peticion_anonima_sin_consultar_contrato(self):
        request = APIRequestFactory().get('/api/contratos/4/competencia/')
        with patch(f'{MODULO}.expediente') as consultar:
            respuesta = CompetenciaView.as_view()(request, pk=4)
        self.assertEqual(respuesta.status_code, 403)
        consultar.assert_not_called()


class CasoCompetenciaDemoTest(SimpleTestCase):
    def test_proyecta_aviso_con_calendarios_ficticios_y_requiere_verificacion(self):
        for hoy in (date(2026, 11, 24), date(2026, 12, 31), date(2028, 2, 29), date(2028, 4, 18)):
            with self.subTest(hoy=hoy):
                terminacion, anios = plan_competencia_demo(hoy, calendario=calendario_nacional)

                def calendario_ficticio(anio):
                    self.assertIn(anio, anios)
                    dias, fuente = calendario_nacional(anio)
                    version = SimpleNamespace(pk=anio, version=1, estado='aprobado')
                    return DiasCalendario(dias, version=version), fuente

                insumos = {
                    'fecha_terminacion': terminacion.isoformat(), 'origen': 'ejecucion', 'sujeto': 'si',
                    'regimen': 'art11_cpaca', 'pacto': 'no', 'liquidacion': 'ninguna', 'incidencias': 'no',
                }
                estado, resultado = evaluar(insumos, calendario=calendario_ficticio)
                self.assertEqual(estado, Estado.PENDIENTE)
                with patch('gestion_contractual.calendarios_servicios.calendario_disponible', calendario_ficticio):
                    vigencia = evaluar_dependencias(resultado)
                self.assertEqual(vigencia['clasificacion'], 'sin_impacto')
                self.assertEqual(evaluar_alertas(hoy=hoy), [])
                avisos = evaluar_alertas(hoy=hoy, fecha_juridica=resultado['limite_competencia'])
                self.assertEqual([aviso['tipo'] for aviso in avisos], ['competencia'])
                self.assertGreater(avisos[0]['dias_restantes'], 0)

    def test_el_caso_requiere_plantillas_ficticias_antes_de_consultar_base(self):
        modulo = 'gestion_contractual.management.commands.sembrar_demo'
        with patch(f'{modulo}.verificar_aislamiento') as conexion, self.assertRaisesMessage(CommandError, 'plantillas ficticias'):
            Command().handle(plantillas='auto', caso_competencia=True)
        conexion.assert_not_called()
