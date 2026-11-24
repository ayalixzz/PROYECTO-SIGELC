from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from .. import mcp_demo


@override_settings(SETTINGS_MODULE='config.settings_hospedado', SIGELC_DEMO=True, DEBUG=False,
                   SIGELC_MCP_ENTREGAS=(11, 12))
class MCPHospedadoTest(SimpleTestCase):
    def conexion(self, *, motor='config.postgres_supabase'):
        db = MagicMock(settings_dict={'ENGINE': motor})
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('postgres', 'sigelc_demo_app', 'sigelc_demo')
        db.connection.pgconn.ssl_in_use = True
        return db, cursor

    def test_exige_el_perfil_y_un_motor_supabase_aislado(self):
        db, cursor = self.conexion()
        with patch.object(mcp_demo, 'connection', db):
            mcp_demo.verificar_base()
            cursor.execute.assert_called_once_with('SELECT current_database(), current_user, current_schema()')
            for ajustes in (
                {'SETTINGS_MODULE': 'config.settings_supabase'},
                {'SIGELC_DEMO': False},
                {'DEBUG': True},
                {'SIGELC_MCP_ENTREGAS': ()},
                {'SIGELC_MCP_ENTREGAS': (11, 11)},
            ):
                with override_settings(**ajustes), self.assertRaises(ValidationError):
                    mcp_demo.verificar_base()
            db.settings_dict['ENGINE'] = 'django.db.backends.postgresql'
            with self.assertRaises(ValidationError):
                mcp_demo.verificar_base()
            self.assertEqual(cursor.execute.call_count, 1)

    def test_rechaza_destino_incorrecto_o_sin_tls(self):
        db, cursor = self.conexion()
        with patch.object(mcp_demo, 'connection', db):
            for datos in (
                ('sigelc_db', 'sigelc_demo_app', 'sigelc_demo'),
                ('postgres', 'postgres', 'sigelc_demo'),
                ('postgres', 'sigelc_demo_app', 'public'),
            ):
                cursor.fetchone.return_value = datos
                with self.assertRaisesMessage(ValidationError, 'La identidad o TLS'):
                    mcp_demo.verificar_base()
            cursor.fetchone.return_value = ('postgres', 'sigelc_demo_app', 'sigelc_demo')
            db.connection.pgconn.ssl_in_use = False
            with self.assertRaisesMessage(ValidationError, 'La identidad o TLS'):
                mcp_demo.verificar_base()

    def test_dpapi_se_mantiene_fuera_del_perfil_hospedado(self):
        db, _ = self.conexion()
        with patch.object(mcp_demo, 'connection', db), patch.object(mcp_demo, 'leer_autorizacion') as leer:
            with self.assertRaisesMessage(ValidationError, 'exclusivamente con la demo local'):
                mcp_demo.acceso()
            leer.assert_not_called()

    def test_identidad_hospedada_exige_entidad_y_sello_de_siembra(self):
        usuario = MagicMock(pk=7, is_superuser=False)
        with (
            patch.object(mcp_demo, 'es_abogado_operativo', return_value=True),
            patch.object(mcp_demo, 'Contrato') as contratos,
            patch.object(mcp_demo, 'Auditoria') as auditorias,
        ):
            propiedad = contratos.objects.filter.return_value
            candidato = propiedad.filter.return_value.select_related.return_value.first.return_value
            sello = auditorias.objects.filter.return_value.filter.return_value
            sello.exists.return_value = True
            self.assertIs(mcp_demo.expediente_ficticio(usuario), candidato)
            propiedad.filter.assert_called_once_with(entidad__nit=mcp_demo.NIT_DEMO)
            auditorias.objects.filter.return_value.filter.assert_called_once_with(
                datos_nuevos__identificador_demo=mcp_demo.NUMERO,
            )
            sello.exists.return_value = False
            with self.assertRaisesMessage(ValidationError, 'expediente ficticio provisionado'):
                mcp_demo.expediente_ficticio(usuario)

    def test_entrega_posterior_no_es_parte_de_los_paquetes_hospedados(self):
        contrato = MagicMock()
        consulta = contrato.revisiones_inteligentes.filter.return_value.values_list
        consulta.return_value = list(mcp_demo.OPERACIONES_HOSPEDADAS)
        mcp_demo.verificar_entregas_hospedadas(contrato, (11, 12))
        contrato.revisiones_inteligentes.filter.assert_called_once_with(pk__in=(11, 12))
        for version in (2, 3):
            consulta.return_value = [min(mcp_demo.OPERACIONES_HOSPEDADAS), (version, uuid4())]
            with self.assertRaisesMessage(ValidationError, 'las entregas ficticias originales'):
                mcp_demo.verificar_entregas_hospedadas(contrato, (11, 14))
        consulta.return_value = []
        with self.assertRaises(ValidationError):
            mcp_demo.verificar_entregas_hospedadas(contrato, (11, 12))
