from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from ..mcp_demo import NUMERO
from ..revision_claude import estado_claude


@override_settings(SIGELC_DEMO=True, SIGELC_CLAUDE_ENTREGAS=())
class ConsultaClaudeTest(SimpleTestCase):
    def consulta(self, *, version=1, numero=NUMERO, identificacion='FICTICIO-001', nit='SIGELC-DEMO-000'):
        contrato = SimpleNamespace(numero_contrato=numero, contratista_identificacion=identificacion,
                                   entidad=SimpleNamespace(nit=nit))
        revision = SimpleNamespace(pk=1, version=version, contrato=contrato)
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
