from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from ..mcp_demo import NUMERO, OPERACIONES_HOSPEDADAS, PERFIL_HOSPEDADO
from ..revision_claude import estado_claude


@override_settings(SIGELC_DEMO=True, SIGELC_CLAUDE_ENTREGAS=())
class ConsultaClaudeTest(SimpleTestCase):
    def consulta(self, *, version=1, numero=NUMERO, identificacion='FICTICIO-001', nit='SIGELC-DEMO-000',
                 pk=1, operacion=None):
        contrato = SimpleNamespace(numero_contrato=numero, contratista_identificacion=identificacion,
                                   entidad=SimpleNamespace(nit=nit))
        revision = SimpleNamespace(pk=pk, version=version, contrato=contrato, operacion=operacion)
        return estado_claude(revision, None, analizada=True, es_vigente=True, puede_decidir=True)

    @override_settings(SIGELC_CLAUDE_CONSULTA_LOCAL=False)
    def test_desactiva_enlace_fuera_de_demo_local(self):
        self.assertIsNone(self.consulta())

    @override_settings(SIGELC_CLAUDE_CONSULTA_LOCAL=True)
    def test_conserva_consulta_ficticia_sin_habilitar_solicitudes(self):
        for version in (1, 2):
            self.assertEqual(self.consulta(version=version), {
                'puede_consultar': True, 'puede_solicitar': False, 'solicitud': None,
            })
        self.assertIsNone(self.consulta(version=3))
        self.assertIsNone(self.consulta(numero='OTRO-EXPEDIENTE'))
        self.assertIsNone(self.consulta(identificacion='FICTICIO-DIFERENTE'))
        self.assertIsNone(self.consulta(nit='OTRA-ENTIDAD'))

    @override_settings(SETTINGS_MODULE=PERFIL_HOSPEDADO, SIGELC_MCP_ENTREGAS=(1, 2),
                       ALLOWED_HOSTS=['sigelc-revision.onrender.com'])
    def test_render_publica_solo_conector_para_las_entregas_ficticias_originales(self):
        operacion = dict(OPERACIONES_HOSPEDADAS)[1]
        self.assertEqual(self.consulta(operacion=operacion), {
            'puede_consultar': True, 'puede_solicitar': False, 'solicitud': None,
            'conector_url': 'https://sigelc-revision.onrender.com/mcp',
        })
        self.assertIsNone(self.consulta(operacion=uuid4()))
        self.assertIsNone(self.consulta(pk=3, operacion=operacion))
        self.assertIsNone(self.consulta(operacion=operacion, nit='OTRA-ENTIDAD'))
