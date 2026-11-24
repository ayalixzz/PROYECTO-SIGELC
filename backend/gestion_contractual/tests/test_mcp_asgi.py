from importlib.util import find_spec
from unittest import skipUnless

from django.test import SimpleTestCase


@skipUnless(find_spec('mcp'), 'Requiere el SDK MCP opcional.')
class TransporteMCPHospedadoTest(SimpleTestCase):
    def test_rutas_y_autenticacion_en_el_mismo_dominio(self):
        from starlette.responses import JSONResponse
        from starlette.testclient import TestClient

        from ..mcp_transporte import combinar_aplicaciones, crear_aplicacion

        async def django(scope, receive, send):
            respuesta = JSONResponse({'ruta_django': scope['path']})
            await respuesta(scope, receive, send)

        dominio = 'https://sigelc-revision.onrender.com'
        mcp = crear_aplicacion(
            url_publica=dominio, puerto=443, entregas=(1, 2), permitir_loopback=False,
        )
        with TestClient(combinar_aplicaciones(django, mcp), base_url=dominio) as cliente:
            self.assertEqual(cliente.get('/api/health/').json(), {'ruta_django': '/api/health/'})
            self.assertEqual(cliente.get('/mcp-extra').json(), {'ruta_django': '/mcp-extra'})
            metadatos = cliente.get('/.well-known/oauth-protected-resource/mcp')
            self.assertEqual(metadatos.status_code, 200)
            self.assertEqual(metadatos.json()['resource'], f'{dominio}/mcp')
            self.assertEqual(metadatos.json()['scopes_supported'], ['sigelc:read'])
            anonimo = cliente.post('/mcp', headers={'Accept': 'application/json, text/event-stream'})
            self.assertEqual(anonimo.status_code, 401)
            self.assertIn(f'{dominio}/.well-known/oauth-protected-resource/mcp',
                          anonimo.headers['WWW-Authenticate'])
            for cabecera in ({'Host': '127.0.0.1:443'}, {'Origin': 'https://ajeno.example'}):
                self.assertEqual(cliente.post('/mcp', headers=cabecera).status_code, 403)
