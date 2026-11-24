from importlib.util import find_spec
from time import time
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

@skipUnless(find_spec('mcp'), 'Requiere el SDK MCP opcional.')
class LimiteMCPTest(SimpleTestCase):
    async def test_corta_una_respuesta_que_supera_el_techo(self):
        from mcp.server.fastmcp.exceptions import ToolError

        from .. import mcp_tools

        async def sigelc_buscar_contratos():
            return {'resultados': ['x' * (24 * 1024)]}

        with patch.object(mcp_tools, 'sigelc_buscar_contratos', sigelc_buscar_contratos):
            servidor = mcp_tools.crear_mcp(acceso=lambda nombre: None, max_resultado=24 * 1024)
            with self.assertRaisesRegex(ToolError, 'supera el límite'):
                await servidor.call_tool('sigelc_buscar_contratos', {})


@skipUnless(find_spec('mcp'), 'Requiere el SDK MCP opcional.')
class OAuthPropuestasTest(SimpleTestCase):
    def test_metadatos_distinguen_alcance_ofrecido_del_requerido(self):
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from ..mcp_oauth import LECTURA, PROPUESTAS, ProveedorOAuth

        for habilitado, esperados in ((False, [LECTURA]), (True, [LECTURA, PROPUESTAS])):
            with self.subTest(permitir_propuestas=habilitado):
                proveedor = ProveedorOAuth('http://127.0.0.1:8765', [2], permitir_propuestas=habilitado)
                app = Starlette()
                proveedor.configure(app)
                with TestClient(app, base_url=proveedor.issuer) as cliente:
                    recurso = cliente.get('/.well-known/oauth-protected-resource/mcp')
                    servidor = cliente.get('/.well-known/oauth-authorization-server')
                self.assertEqual(recurso.json()['scopes_supported'], esperados)
                self.assertEqual(servidor.json()['scopes_supported'], esperados)
                self.assertEqual(proveedor.auth_settings().required_scopes, [LECTURA])

    def test_el_consentimiento_de_propuestas_exige_confirmacion_separada(self):
        from mcp.server.auth.provider import AuthorizationParams
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyHttpUrl
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from ..mcp_oauth import CLAUDE_CALLBACK, LECTURA, PROPUESTAS, ProveedorOAuth

        proveedor = ProveedorOAuth('http://127.0.0.1:8765', [2], permitir_propuestas=True)
        app = Starlette()
        proveedor.configure(app)
        cliente_oauth = OAuthClientInformationFull(client_id='test', redirect_uris=[AnyHttpUrl(CLAUDE_CALLBACK)])
        parametros = AuthorizationParams(
            state='estado-de-prueba', scopes=[LECTURA, PROPUESTAS], code_challenge='A' * 43,
            redirect_uri=cliente_oauth.redirect_uris[0], redirect_uri_provided_explicitly=True,
            resource=proveedor.resource,
        )
        from asgiref.sync import async_to_sync

        direccion = async_to_sync(proveedor.authorize)(cliente_oauth, parametros)
        solicitud = parse_qs(urlsplit(direccion).query)['solicitud'][0]
        with TestClient(app, base_url=proveedor.issuer) as cliente:
            pagina = cliente.get(direccion)
            self.assertIn('name="propuestas"', pagina.text)
            formulario = {
                'solicitud': solicitud, 'csrf': cliente.cookies['sigelc_oauth_csrf'],
                'usuario': 'abogado-demo', 'contrasena': 'ensayo', 'consentir': 'si',
            }
            respuesta = cliente.post('/oauth/consentir', data=formulario, headers={'Origin': proveedor.issuer})
        self.assertEqual(respuesta.status_code, 400)

    def test_entrega_exige_alcance_y_permiso_actual(self):
        from types import SimpleNamespace

        from ..mcp_oauth import Identidad, LECTURA, PROPUESTAS, ProveedorOAuth

        proveedor = ProveedorOAuth('http://127.0.0.1:8765', [2], permitir_propuestas=True)
        identidad = Identidad(3, 'credencial-de-ensayo', 1, 2, (2,))
        ahora = int(time())
        read_only = proveedor._emitir('cliente', identidad, [LECTURA], ahora, ahora + 900)
        propuestas = proveedor._emitir('cliente', identidad, [LECTURA, PROPUESTAS], ahora, ahora + 900)
        for token, permiso_django, permitido in (
            (read_only, True, False), (propuestas, False, False), (propuestas, True, True),
        ):
            with (
                patch('gestion_contractual.mcp_oauth.get_access_token',
                      return_value=SimpleNamespace(token=token.access_token)),
                patch('gestion_contractual.mcp_oauth._identidad_vigente',
                      return_value=(SimpleNamespace(has_perm=lambda nombre: permiso_django), object())),
                patch('gestion_contractual.mcp_oauth._auditar'),
            ):
                if permitido:
                    _, _, permiso = proveedor.acceso_actual('sigelc_entregar_propuestas')
                    self.assertTrue(permiso['propuestas'])
                    self.assertFalse(permiso['escritura'])
                else:
                    with self.assertRaisesRegex(ValidationError, 'para devolver propuestas'):
                        proveedor.acceso_actual('sigelc_entregar_propuestas')

    async def test_herramientas_default_local_y_remoto_habilitado(self):
        from types import SimpleNamespace

        from mcp.server.fastmcp.exceptions import ToolError

        from ..mcp_tools import crear_mcp

        configuraciones = [
            ({}, 5, set()),
            ({'escritura': True}, 6, {'sigelc_guardar_propuestas'}),
            ({'recibir_propuestas': True}, 6, {'sigelc_entregar_propuestas'}),
        ]
        for opciones, total, extras in configuraciones:
            servidor = crear_mcp(acceso=lambda nombre: None, **opciones)
            herramientas = {t.name for t in await servidor.list_tools()}
            self.assertEqual(len(herramientas), total)
            self.assertEqual(
                herramientas & {'sigelc_guardar_propuestas', 'sigelc_entregar_propuestas'}, extras,
            )
        contrato = SimpleNamespace(pk=81)
        solicitud = uuid4()
        entrada = {'contrato_id': 81, 'entrega_id': 2, 'solicitud_id': str(solicitud), 'propuestas': []}
        servidor = crear_mcp(acceso=lambda nombre: (object(), contrato, {}), recibir_propuestas=True)
        with self.assertRaisesRegex(ToolError, 'para devolver propuestas'):
            await servidor.call_tool('sigelc_entregar_propuestas', entrada)

        usuario, revision = object(), object()
        permiso = {'propuestas': True, 'entregas': (2,)}
        servidor = crear_mcp(acceso=lambda nombre: (usuario, contrato, permiso), recibir_propuestas=True)
        with (
            patch('gestion_contractual.mcp_demo.entrega', return_value=revision) as validar_entrega,
            patch('gestion_contractual.revision_claude.recibir_propuestas',
                  return_value={'solicitud_id': str(solicitud), 'estado': 'recibida', 'propuestas': 0}) as recibir,
        ):
            await servidor.call_tool('sigelc_entregar_propuestas', entrada)
            validar_entrega.assert_called_once_with(contrato, permiso, 2)
            recibir.assert_called_once_with(usuario=usuario, revision=revision, solicitud_id=solicitud, propuestas=[])
