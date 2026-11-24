from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from gestion_contractual.alertas_politica import evaluar_alertas
from gestion_contractual.calendario import DiasCalendario


def calendario(anio, festivos=(), excepciones=(), *, aprobado=True):
    version = SimpleNamespace(pk=anio, version=1, estado='aprobado') if aprobado else None
    return DiasCalendario(festivos, version=version, excepciones=excepciones), f'Calendario ficticio {anio}'


def confirmado(fecha):
    return {'estado': 'confirmado', 'fecha': fecha}


class AlertasPoliticaTest(SimpleTestCase):
    def test_competencia_usa_calendario_incluso_en_fin_de_semana(self):
        fecha = date(2026, 1, 8)
        proveedor = Mock()
        alerta, = evaluar_alertas(
            hoy=date(2026, 1, 3), fecha_juridica=fecha,
            proveedor_calendario=proveedor,
        )
        self.assertEqual(
            (alerta['tipo'], alerta['hito'], alerta['dias_restantes'], alerta['unidad']),
            ('competencia', '7', 5, 'calendario'),
        )
        proveedor.assert_not_called()

    def test_competencia_incluye_solo_el_hito_vigente_si_se_omitio_un_dia(self):
        hoy = date(2026, 1, 5)
        casos = (
            (31, None), (30, '30'), (16, '30'), (15, '15'), (8, '15'),
            (7, '7'), (2, '7'), (1, '1'), (0, 'hoy'), (-1, 'vencida'),
        )
        for dias, hito in casos:
            with self.subTest(dias=dias):
                alertas = evaluar_alertas(hoy=hoy, fecha_juridica=(hoy + timedelta(days=dias)).isoformat())
                if hito is None:
                    self.assertEqual(alertas, [])
                else:
                    alerta, = alertas
                    self.assertEqual(alerta['hito'], hito)
                    self.assertEqual(alerta['dias_restantes'], dias)

    def test_operativa_descuenta_festivo_y_fin_de_semana(self):
        hoy = date(2026, 1, 5)
        proveedor = Mock(return_value=calendario(2026, [date(2026, 1, 7)]))
        alerta, = evaluar_alertas(
            hoy=hoy, vencimiento_operativo=confirmado('2026-01-13'),
            proveedor_calendario=proveedor,
        )
        self.assertEqual(
            (alerta['tipo'], alerta['hito'], alerta['dias_restantes'], alerta['unidad']),
            ('operativa', '5', 5, 'habiles'),
        )
        self.assertEqual(alerta['fecha_objetivo'], '2026-01-13')
        proveedor.assert_called_once_with(2026)

    def test_operativa_no_duplica_hito_cuando_dias_cambian_en_misma_franja(self):
        objetivo = confirmado(date(2026, 1, 9))
        proveedor = Mock(return_value=calendario(2026))
        primera, = evaluar_alertas(hoy=date(2026, 1, 6), vencimiento_operativo=objetivo,
                                   proveedor_calendario=proveedor)
        segunda, = evaluar_alertas(hoy=date(2026, 1, 7), vencimiento_operativo=objetivo,
                                   proveedor_calendario=proveedor)
        self.assertEqual((primera['dias_restantes'], segunda['dias_restantes']), (3, 2))
        self.assertEqual(primera['clave'], segunda['clave'])
        self.assertEqual(primera['hito'], '3')
        ultima, = evaluar_alertas(hoy=date(2026, 1, 8), vencimiento_operativo=objetivo,
                                  proveedor_calendario=proveedor)
        self.assertNotEqual(segunda['clave'], ultima['clave'])
        self.assertEqual(ultima['hito'], '1')

    def test_excepciones_del_calendario_y_cambio_de_anio(self):
        hoy, vencimiento = date(2026, 12, 30), date(2027, 1, 4)
        calendarios = {
            2026: calendario(2026),
            2027: calendario(2027, [date(2027, 1, 1)], [{'fecha': '2027-01-02', 'habil': True}]),
        }
        proveedor = Mock(side_effect=calendarios.__getitem__)
        alerta, = evaluar_alertas(
            hoy=hoy, vencimiento_operativo=confirmado(vencimiento), proveedor_calendario=proveedor,
        )
        self.assertEqual((alerta['dias_restantes'], alerta['hito']), (3, '3'))
        self.assertEqual([llamada.args for llamada in proveedor.call_args_list], [(2026,), (2027,)])

        calendarios[2027] = calendario(2027, aprobado=False)
        alerta, = evaluar_alertas(
            hoy=hoy, fecha_juridica=vencimiento, vencimiento_operativo=confirmado(vencimiento),
            proveedor_calendario=proveedor,
        )
        self.assertEqual(alerta['tipo'], 'competencia')

    def test_no_generar_alerta_desde_fecha_operativa_provisional_o_incierta(self):
        proveedor = Mock()
        for estado in ('provisional', 'requiere_revision', None):
            with self.subTest(estado=estado):
                self.assertEqual(evaluar_alertas(
                    hoy=date(2026, 1, 8),
                    vencimiento_operativo={'estado': estado, 'fecha': '2026-01-09'},
                    proveedor_calendario=proveedor,
                ), [])
        proveedor.assert_not_called()

    def test_no_propone_plazo_sustituto_para_vencimiento_en_dia_inhabil(self):
        proveedor = Mock(return_value=calendario(2026))
        self.assertEqual(evaluar_alertas(
            hoy=date(2026, 1, 8), vencimiento_operativo=confirmado('2026-01-10'),
            proveedor_calendario=proveedor,
        ), [])

    def test_vencida_operativa_es_persistente_y_no_recalcula_pasado(self):
        proveedor = Mock()
        objetivo = confirmado('2026-01-09')
        hoy, = evaluar_alertas(hoy=date(2026, 1, 9), vencimiento_operativo=objetivo,
                               proveedor_calendario=proveedor)
        primera, = evaluar_alertas(hoy=date(2026, 1, 10), vencimiento_operativo=objetivo,
                                   proveedor_calendario=proveedor)
        siguiente, = evaluar_alertas(hoy=date(2026, 1, 12), vencimiento_operativo=objetivo,
                                     proveedor_calendario=proveedor)
        self.assertEqual((hoy['hito'], primera['hito'], siguiente['hito']), ('hoy', 'vencida', 'vencida'))
        self.assertIsNone(siguiente['dias_restantes'])
        self.assertEqual(primera['clave'], siguiente['clave'])
        proveedor.assert_not_called()

    def test_competencia_critica_antes_del_vencimiento_operativo(self):
        hoy = date(2026, 1, 5)
        alertas = evaluar_alertas(
            hoy=hoy, fecha_juridica='2026-01-04', vencimiento_operativo=confirmado(hoy),
        )
        self.assertEqual([(a['tipo'], a['prioridad']) for a in alertas], [
            ('competencia', 'critica'), ('operativa', 'alta'),
        ])

    def test_no_genera_alertas_sobre_fechas_ausentes_o_no_validas(self):
        hoy = date(2026, 1, 5)
        self.assertEqual(evaluar_alertas(
            hoy=hoy, fecha_juridica={'estado': 'preliminar', 'fecha': '2026-01-06'},
            vencimiento_operativo=confirmado('desconocida'),
        ), [])
        with self.assertRaises(ValueError):
            evaluar_alertas(hoy=datetime(2026, 1, 5))

    def test_calendario_se_consulta_solo_en_ventana_necesaria(self):
        proveedor = Mock(return_value=calendario(2026))
        resultado = evaluar_alertas(hoy=date(2026, 1, 5), vencimiento_operativo=confirmado('2099-01-13'),
                                    proveedor_calendario=proveedor)
        self.assertEqual(resultado, [])
        proveedor.assert_called_once_with(2026)
