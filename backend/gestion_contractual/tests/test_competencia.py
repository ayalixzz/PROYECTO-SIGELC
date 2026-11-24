from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from gestion_contractual.competencia import Estado, evaluar, fecha_confirmada, registrar_calculo, sumar_anios, sumar_meses
from gestion_contractual.models import Auditoria
from . import test_requerimientos


INSUMOS = {'sujeto': 'si', 'regimen': 'art11_cpaca', 'origen': 'ejecucion',
           'fecha_terminacion': '2025-02-10', 'pacto': 'no', 'liquidacion': 'ninguna', 'incidencias': 'no'}


class ComputoTest(SimpleTestCase):
    def test_meses_y_anios_calendario(self):
        for inicio, fin in [
            (date(2023, 1, 31), date(2023, 2, 28)), (date(2024, 1, 31), date(2024, 2, 29)),
            (date(2024, 3, 31), date(2024, 4, 30)), (date(2024, 12, 31), date(2025, 1, 31)),
        ]:
            with self.subTest(inicio=inicio):
                self.assertEqual(sumar_meses(inicio, 1), fin)
        self.assertEqual(sumar_anios(date(2024, 2, 29), 2), date(2026, 2, 28))

    def test_supletivo_y_ventanas_distintas(self):
        estado, r = evaluar({**INSUMOS, 'fecha_terminacion': '2025-02-12'}, calendario=lambda anio: (set(), 'Fixture'))
        self.assertEqual(estado, Estado.PENDIENTE)
        self.assertEqual([r[k] for k in ('fin_bilateral', 'fin_unilateral', 'inicio_judicial', 'fin_judicial', 'limite_competencia')],
                         ['2025-06-12', '2025-08-12', '2025-08-13', '2027-08-13', '2027-08-12'])

    def test_pacto_y_fuentes_de_fecha_base(self):
        datos = {**INSUMOS, 'pacto': 'si', 'cantidad': 3, 'unidad': 'meses', 'referencia_pacto': '2025-02-11'}
        for origen, adicional in [
            ('efectiva', {'fecha_efectiva': '2025-02-11'}),
            ('acto', {'fecha_referencia': '2025-02-11'}),
            ('acuerdo', {'fecha_referencia': '2025-02-11'}),
        ]:
            _, r = evaluar({**datos, **adicional, 'origen': origen, 'referencias': 'Referencia del expediente'}, calendario=lambda _: (set(), 'Fixture'))
            self.assertEqual(r['fecha_base'], '2025-02-11')
            self.assertEqual(r['fin_bilateral'], '2025-05-11')

    def test_supuestos_que_requieren_revision_o_informacion(self):
        for datos, estado in [
            ({'fecha_terminacion': None}, Estado.INFORMACION),
            ({'pacto': 'si'}, Estado.INFORMACION),
            ({'incidencias': ''}, Estado.INFORMACION),
            ({'regimen': 'otro'}, Estado.REVISION),
            ({'sujeto': 'no'}, Estado.PENDIENTE),
            ({'incidencias': 'si'}, Estado.REVISION),
            ({'liquidacion': 'unilateral'}, Estado.INFORMACION),
            ({'liquidacion': 'bilateral', 'fecha_liquidacion': '2025-08-13', 'firma_acta': '2025-08-13'}, Estado.REVISION),
        ]:
            with self.subTest(datos=datos):
                obtenido, r = evaluar({**INSUMOS, **datos}, calendario=lambda _: (set(), 'Fixture'))
                self.assertEqual(obtenido, estado)
                self.assertIsNone(r['limite_competencia'])

    def test_liquidacion_previa_usa_firma_o_ejecutoria(self):
        base = {**INSUMOS, 'fecha_terminacion': '2025-02-12'}
        for previa, datos, fecha in [
            ('bilateral', {'fecha_liquidacion': '2025-06-09', 'firma_acta': '2025-06-10'}, '2025-06-11'),
            ('unilateral', {'fecha_liquidacion': '2025-07-01', 'ejecutoria_acto': '2025-07-06'}, '2025-07-07'),
        ]:
            estado, r = evaluar({**base, **datos, 'liquidacion': previa}, calendario=lambda _: (set(), 'Fixture'))
            self.assertEqual(estado, Estado.PENDIENTE, r['advertencias'])
            self.assertEqual(r['inicio_judicial'], fecha)
            self.assertIsNone(r['limite_competencia'])

    def test_feriado_detectado_sin_traslado_automatico(self):
        estado, r = evaluar({**INSUMOS, 'fecha_terminacion': '2025-02-12'},
                            calendario=lambda _: ({date(2025, 6, 12)}, 'Fixture'))
        self.assertEqual(estado, Estado.REVISION)
        self.assertIsNone(r['limite_competencia'])


class VersionesCompetenciaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_requerimientos.RequerimientosTest.setUpTestData.__func__(cls)
        from .calendarios_ficticios import aprobar_regresion
        aprobar_regresion(cls.abogado)

    def guardar(self, **opciones):
        datos = {'contrato_id': self.contrato.pk, 'usuario': self.abogado, 'operacion': uuid4(), 'anterior_id': None}
        return registrar_calculo(**(datos | opciones))

    def test_versiones_verificacion_reintento_y_rectificacion(self):
        self.contrato.fecha_terminacion = date(2025, 2, 12)
        self.contrato.save()
        clave = uuid4()
        datos = {k: v for k, v in INSUMOS.items() if k != 'fecha_terminacion'}
        primero = self.guardar(datos=datos, operacion=clave)
        self.assertEqual(self.guardar(datos=datos, operacion=clave).pk, primero.pk)
        self.assertIsNone(fecha_confirmada(self.contrato))
        with self.assertRaises(ValidationError):
            self.guardar(anterior_id=primero.pk, accion='verificar')
        with self.assertRaises(PermissionDenied):
            self.guardar(anterior_id=primero.pk, accion='verificar', fundamento='Fundamento', usuario=self.otro)
        verificado = self.guardar(anterior_id=primero.pk, accion='verificar', fundamento='Contrastado con los soportes')
        self.assertEqual(verificado.verificado_por, self.abogado)
        self.assertEqual(fecha_confirmada(self.contrato), date(2027, 8, 12))
        from gestion_contractual.competencia import FECHAS_RESULTADO
        fechas = {k: verificado.resultado[k] for k in FECHAS_RESULTADO}
        with self.assertRaises(ValidationError):
            self.guardar(anterior_id=primero.pk, accion='rectificar', motivo='Motivo', fundamento='Fundamento', rectificacion=fechas)
        rectificada = self.guardar(anterior_id=verificado.pk, accion='rectificar', motivo='Motivo', fundamento='Fundamento',
                                   rectificacion={**fechas, 'limite_competencia': '2027-08-13'})
        self.assertEqual(rectificada.anterior, verificado)
        self.assertEqual(rectificada.estado, Estado.PENDIENTE)
        self.assertIsNone(fecha_confirmada(self.contrato))
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual, self.estado)

    def test_admin_genera_propuesta_y_registra_pendientes(self):
        self.client.force_login(self.abogado)
        url = reverse('admin:gestion_contractual_competencia', args=[self.contrato.pk])
        self.assertContains(self.client.get(url), 'Calcular propuesta')
        r = self.client.post(url, {'accion': 'calcular', 'operacion': uuid4(), 'sujeto': 'si', 'regimen': 'art11_cpaca'})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(url), 'Requiere información')
        self.assertEqual(self.contrato.calculos_competencia.count(), 1)

    def test_actualizacion_invalida_fecha_confirmada_y_conserva_version(self):
        from gestion_contractual.competencia import actualizar_por_edicion
        self.contrato.fecha_terminacion = date(2025, 2, 12)
        self.contrato.save()
        primero = self.guardar(datos={k: v for k, v in INSUMOS.items() if k != 'fecha_terminacion'})
        verificado = self.guardar(anterior_id=primero.pk, accion='verificar', fundamento='Verificado')
        self.contrato.fecha_terminacion = date(2025, 2, 13)
        self.contrato.save()
        self.assertIsNone(fecha_confirmada(self.contrato))
        nuevo = actualizar_por_edicion(self.contrato, self.abogado)
        self.assertEqual(nuevo.anterior, verificado)
        self.assertEqual(nuevo.cambios, ['fecha_terminacion'])
        self.assertIsNone(nuevo.verificado_por)
        self.assertEqual(nuevo.estado, Estado.REVISION)
        self.assertEqual(verificado.estado, Estado.VERIFICADO)

    def test_rollback_si_falla_auditoria(self):
        with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('Fallo simulado')):
            with self.assertRaises(RuntimeError):
                self.guardar(datos={})
        self.assertFalse(self.contrato.calculos_competencia.exists())
