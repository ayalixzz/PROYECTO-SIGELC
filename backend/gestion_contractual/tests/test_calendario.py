from datetime import date
from django.test import SimpleTestCase
from gestion_contractual.calendario import calcular_vencimiento, calendario_nacional, es_dia_habil


class CalendarioTest(SimpleTestCase):
    def test_fines_de_semana_y_festivo_suministrado(self):
        festivos = {date(2026, 1, 1)}
        self.assertFalse(es_dia_habil(date(2026, 1, 1), festivos_verificados=festivos))
        self.assertTrue(es_dia_habil(date(2026, 1, 2), festivos_verificados=festivos))
        self.assertFalse(es_dia_habil(date(2026, 1, 3), festivos_verificados=festivos))
        self.assertFalse(es_dia_habil(date(2026, 1, 4), festivos_verificados=festivos))

    def test_calendario_explicito_obligatorio(self):
        with self.assertRaises(ValueError):
            es_dia_habil(date(2026, 1, 1), festivos_verificados=None)

    def test_vencimientos_desde_el_dia_siguiente(self):
        for inicio, dias, festivos, esperado in [
            (date(2026, 1, 12), 5, set(), date(2026, 1, 19)),
            (date(2026, 1, 16), 2, set(), date(2026, 1, 20)),
            (date(2026, 1, 17), 8, set(), date(2026, 1, 28)),
            (date(2026, 1, 12), 15, {date(2026, 1, 13), date(2026, 1, 14)}, date(2026, 2, 4)),
            (date(2026, 12, 30), 2, {date(2027, 1, 1)}, date(2027, 1, 4)),
        ]:
            with self.subTest(inicio=inicio, dias=dias):
                def proveedor(anio):
                    return {fecha for fecha in festivos if fecha.year == anio}, 'calendario de prueba'
                vencimiento, fuentes = calcular_vencimiento(inicio, dias, proveedor=proveedor)
                self.assertEqual(vencimiento, esperado)
                self.assertIn(str(esperado.year), fuentes)

    def test_festivos_nacionales_y_traslados(self):
        festivos, _ = calendario_nacional(2026)
        self.assertTrue({date(2026, 4, 2), date(2026, 4, 3), date(2026, 7, 13), date(2026, 7, 20)} <= festivos)
        self.assertNotIn(date(2026, 7, 9), festivos)
        self.assertNotIn(date(2025, 7, 14), calendario_nacional(2025)[0])
        self.assertEqual(calcular_vencimiento(date(2026, 7, 10), 2)[0], date(2026, 7, 15))
        self.assertEqual(calcular_vencimiento(date(2026, 7, 10), None), (None, {}))
