"""Independent Streamable HTTP and OAuth pilot test. All connector calls are reads."""

import argparse
import asyncio
from base64 import urlsafe_b64encode
from getpass import getpass
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
import ssl
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ALCANCE = 'sigelc:read'
CALLBACK = 'https://claude.ai/api/mcp/auth_callback'
HERRAMIENTAS = {
    'sigelc_buscar_contratos', 'sigelc_consultar_expediente', 'sigelc_consultar_entrega',
    'sigelc_comparar_entregas', 'sigelc_consultar_observaciones',
}
LIMITE_BYTES_HERRAMIENTA = 24 * 1024
LIMITE_BYTES_SOLICITUD = 64 * 1024
ANEXOS_PRIVADOS = {'archivo', 'ruta', 'ruta_archivo', 'url_archivo', 'descarga_url', 'media_url'}


def exigir(condicion, mensaje):
    if not condicion:
        raise RuntimeError(mensaje)


class CamposOcultos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.campos = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name') in {'solicitud', 'csrf'}:
            self.campos[attrs['name']] = attrs.get('value')


class Piloto:
    def __init__(self, cliente, base, callback):
        self.cliente = cliente
        self.base = base
        self.recurso = f'{base}/mcp'
        self.callback = callback

    def url_local(self, url):
        destino = urlsplit(urljoin(self.base, url))
        origen = urlsplit(self.base)
        exigir((destino.scheme, destino.netloc) == (origen.scheme, origen.netloc),
               'El servidor anunció una URL fuera de la base configurada.')
        return destino.geturl()

    async def datos(self, ruta):
        respuesta = await self.cliente.get(self.url_local(ruta))
        exigir(respuesta.status_code == 200, f'No respondió el metadato {ruta}.')
        return respuesta.json()

    async def descubrir(self):
        anonimo = await self.cliente.get(self.recurso)
        exigir(anonimo.status_code == 401, 'El recurso MCP debe rechazar el acceso anónimo.')
        ruta_protegida = '/.well-known/oauth-protected-resource/mcp'
        challenge = anonimo.headers.get('www-authenticate', '')
        exigir(f'{self.base}{ruta_protegida}' in challenge, 'Falta el enlace al metadato del recurso protegido.')
        protegido = await self.datos(ruta_protegida)
        exigir(protegido.get('resource') == self.recurso, 'La audiencia publicada no coincide.')
        exigir([issuer.rstrip('/') for issuer in protegido['authorization_servers']] == [self.base],
               'La autoridad anunciada no coincide con la base pública.')
        metadata = await self.datos('/.well-known/oauth-authorization-server')
        exigir(metadata['issuer'].rstrip('/') == self.base, 'El emisor de OAuth no coincide.')
        exigir(metadata.get('code_challenge_methods_supported') == ['S256'],
               'El servidor debe anunciar PKCE S256.')
        exigir(ALCANCE in metadata['scopes_supported'] and 'sigelc:write' not in metadata['scopes_supported'],
               'El piloto debe anunciar solo el alcance de lectura.')
        self.endpoints = {
            clave: self.url_local(metadata[f'{clave}_endpoint'])
            for clave in ('registration', 'authorization', 'token', 'revocation')
        }

    async def verificar_perimetro(self):
        for ruta in ('/admin/', '/api/contratos/', '/static/', '/media/', '/private_media_demo/', '/vite/'):
            respuesta = await self.cliente.get(self.url_local(ruta))
            exigir(respuesta.status_code == 404, f'La ruta interna {ruta} no está cerrada.')
        for encabezados in ({'Host': 'otro.example.invalid'}, {'Origin': 'https://otro.example.invalid'}):
            respuesta = await self.cliente.get(self.recurso, headers=encabezados)
            exigir(respuesta.status_code in (400, 403, 421), 'No se rechazó un Host u Origin ajeno.')
        respuesta = await self.cliente.get(self.recurso, headers={'Authorization': 'Bearer token-invalido'})
        exigir(respuesta.status_code == 401, 'Se admitió un token inválido.')
        respuesta = await self.cliente.post(self.recurso, headers={'Content-Type': 'application/json'},
                                           content=b' ' * (LIMITE_BYTES_SOLICITUD + 1))
        exigir(respuesta.status_code == 413, 'El servidor no rechazó una solicitud mayor que el límite.')

    async def registrar(self):
        self.registro = {
            'client_name': 'SIGELC cliente de prueba',
            'redirect_uris': [self.callback],
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'],
            'token_endpoint_auth_method': 'none',
            'scope': ALCANCE,
        }
        ajeno = await self.cliente.post(self.endpoints['registration'], json={
            **self.registro, 'redirect_uris': ['https://otro.example.invalid/callback'],
        })
        exigir(ajeno.status_code in (400, 403, 422), 'El registro admitió una redirección ajena.')
        respuesta = await self.cliente.post(self.endpoints['registration'], json=self.registro)
        exigir(respuesta.status_code in (200, 201), 'No se registró el cliente OAuth.')
        datos = respuesta.json()
        exigir(datos['token_endpoint_auth_method'] == 'none', 'Se esperaba un cliente público con PKCE.')
        self.client_id = datos['client_id']

    async def verificar_limite(self):
        for _ in range(16):
            respuesta = await self.cliente.post(self.endpoints['registration'], json={
                **self.registro, 'redirect_uris': ['https://otro.example.invalid/callback'],
            })
            if respuesta.status_code == 429:
                return
            exigir(respuesta.status_code in (400, 403, 422), 'Se registró un callback ajeno al comprobar el límite.')
        raise RuntimeError('El registro no aplicó el límite de frecuencia.')

    def es_rechazo_autorizacion(self, respuesta):
        if respuesta.status_code in (400, 403, 422):
            return True
        if respuesta.is_redirect:
            retorno = urlsplit(respuesta.headers['location'])
            return 'error' in parse_qs(retorno.query)
        return False

    async def autorizar(self, usuario, contrasena):
        verificador = secrets.token_urlsafe(48)
        params = {
            'response_type': 'code', 'client_id': self.client_id, 'redirect_uri': self.callback,
            'scope': ALCANCE, 'state': secrets.token_urlsafe(24), 'resource': self.recurso,
            'code_challenge_method': 'S256',
            'code_challenge': urlsafe_b64encode(sha256(verificador.encode()).digest()).decode().rstrip('='),
        }
        for cambio, nombre in (({'resource': f'{self.base}/otro'}, 'audiencia ajena'),
                               ({'code_challenge_method': 'plain'}, 'PKCE plain'),
                               ({'scope': 'sigelc:write'}, 'alcance de escritura')):
            respuesta = await self.cliente.get(self.endpoints['authorization'], params={**params, **cambio})
            exigir(self.es_rechazo_autorizacion(respuesta), f'La autorización admitió {nombre}.')
        respuesta = await self.cliente.get(self.endpoints['authorization'], params=params)
        exigir(respuesta.is_redirect and 'location' in respuesta.headers, 'No se abrió el consentimiento OAuth.')
        consentimiento = self.url_local(respuesta.headers['location'])
        formulario = await self.cliente.get(consentimiento)
        exigir(formulario.status_code == 200, 'El formulario de consentimiento no respondió.')
        exigir(formulario.headers.get('referrer-policy') == 'same-origin',
               'El formulario debe conservar su origen en el POST del navegador.')
        campos = CamposOcultos()
        campos.feed(formulario.text)
        exigir(all(campos.campos.get(campo) for campo in ('solicitud', 'csrf')), 'El formulario no tiene protección CSRF.')
        sin_csrf = await self.cliente.post(consentimiento, headers={'Origin': self.base}, data={
            **campos.campos, 'csrf': 'invalido', 'usuario': usuario, 'consentir': 'si',
        })
        exigir(sin_csrf.status_code in (400, 403), 'El consentimiento admitió un CSRF inválido.')
        respuesta = await self.cliente.post(consentimiento, headers={'Origin': self.base}, data={
            **campos.campos, 'usuario': usuario, 'contrasena': contrasena or secrets.token_urlsafe(32), 'consentir': 'si',
        })
        if contrasena is None:
            exigir(respuesta.status_code == 403 and 'location' not in respuesta.headers,
                   'Se autorizaron credenciales de prueba inválidas.')
            return None
        exigir(respuesta.is_redirect and 'location' in respuesta.headers,
               f'No se autorizó la cuenta ficticia. HTTP {respuesta.status_code}.')
        exigir(respuesta.headers.get('referrer-policy') == 'no-referrer',
               'El callback externo no debe recibir la referencia del consentimiento.')
        callback = urlsplit(respuesta.headers['location'])
        esperado = urlsplit(self.callback)
        exigir((callback.scheme, callback.netloc, callback.path) == (esperado.scheme, esperado.netloc, esperado.path),
               'La redirección final no coincide con el callback registrado.')
        retorno = parse_qs(callback.query)
        exigir(retorno.get('state') == [params['state']] and len(retorno.get('code', [])) == 1,
               'El callback no conservó state o no devolvió un código.')
        return {'grant_type': 'authorization_code', 'client_id': self.client_id,
                'code': retorno['code'][0], 'code_verifier': verificador,
                'redirect_uri': self.callback, 'resource': self.recurso}

    async def intercambiar(self, credenciales):
        for cambio, texto in (({'code_verifier': secrets.token_urlsafe(48)}, 'verificador PKCE incorrecto'),
                              ({'resource': f'{self.base}/otro'}, 'audiencia ajena')):
            invalida = await self.cliente.post(self.endpoints['token'], data={**credenciales, **cambio})
            exigir(invalida.status_code in (400, 401, 403), f'El canje admitió {texto}.')
        resultado = await self.cliente.post(self.endpoints['token'], data=credenciales)
        exigir(resultado.status_code == 200, 'Falló el canje legítimo por token OAuth.')
        tokens = resultado.json()
        exigir(tokens.get('token_type', '').lower() == 'bearer' and tokens.get('access_token'),
               'El canje no entregó un token Bearer.')
        replay = await self.cliente.post(self.endpoints['token'], data=credenciales)
        exigir(replay.status_code in (400, 401, 403), 'Se pudo reutilizar un código de autorización.')
        return tokens

    async def renovar(self, tokens):
        datos = {'grant_type': 'refresh_token', 'client_id': self.client_id,
                 'resource': self.recurso, 'refresh_token': tokens['refresh_token']}
        incorrecto = await self.cliente.post(self.endpoints['token'], data={**datos, 'resource': f'{self.base}/otro'})
        exigir(incorrecto.status_code in (400, 401, 403), 'La renovación admitió otra audiencia.')
        renovado = await self.cliente.post(self.endpoints['token'], data=datos)
        exigir(renovado.status_code == 200, 'Falló la renovación del token.')
        return renovado.json()

    async def revocar(self, token, tipo='access_token'):
        respuesta = await self.cliente.post(self.endpoints['revocation'], data={
            'client_id': self.client_id, 'token': token, 'token_type_hint': tipo,
        })
        exigir(respuesta.status_code == 200, f'Falló la revocación de {tipo}.')

    async def rechazar_renovacion(self, token):
        respuesta = await self.cliente.post(self.endpoints['token'], data={
            'grant_type': 'refresh_token', 'client_id': self.client_id,
            'resource': self.recurso, 'refresh_token': token,
        })
        exigir(respuesta.status_code in (400, 401), 'Se aceptó un token de renovación rotado o revocado.')

    async def comprobar_token(self, token):
        return await self.cliente.post(self.recurso, headers={
            'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream',
        }, json={
            'jsonrpc': '2.0', 'id': 'prueba-token', 'method': 'initialize',
            'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                       'clientInfo': {'name': 'SIGELC interesado', 'version': '1.0'}},
        })


def sin_rutas_privadas(datos):
    if isinstance(datos, dict):
        return not ANEXOS_PRIVADOS.intersection(datos) and all(sin_rutas_privadas(v) for v in datos.values())
    if isinstance(datos, list):
        return all(sin_rutas_privadas(v) for v in datos)
    return True


async def recorrido_mcp(piloto, cliente):
    async with streamable_http_client(piloto.recurso, http_client=cliente) as (lectura, escritura, _):
        async with ClientSession(lectura, escritura) as sesion:
            await sesion.initialize()
            herramientas = {t.name for t in (await sesion.list_tools()).tools}
            exigir(herramientas == HERRAMIENTAS, 'La lista remota debe contener exactamente cinco herramientas de lectura.')

            async def llamar(nombre, **argumentos):
                resultado = await sesion.call_tool(f'sigelc_{nombre}', argumentos)
                exigir(not resultado.isError, f'La herramienta {nombre} rechazó la consulta ficticia.')
                datos = resultado.structuredContent or json.loads(resultado.content[0].text)
                serializado = json.dumps(datos, separators=(',', ':'), ensure_ascii=False).encode()
                exigir(len(serializado) <= LIMITE_BYTES_HERRAMIENTA, f'{nombre} excede el límite de respuesta.')
                exigir(sin_rutas_privadas(datos), f'{nombre} expuso datos de almacenamiento.')
                return datos

            listado = await llamar('buscar_contratos', texto_busqueda='', limite=12)
            exigir([c['numero'] for c in listado['resultados']] == ['SIGELC-DEMO-001'],
                   'El listado reveló un contrato fuera del piloto.')
            contrato = listado['resultados'][0]['id']
            ficha = await llamar('consultar_expediente', contrato_id=contrato)
            entregas = ficha['entregas'][:2]
            exigir(len(entregas) == 2, 'Se requieren dos entregas ficticias autorizadas.')
            for entrega in entregas:
                detalle = await llamar('consultar_entrega', contrato_id=contrato, entrega_id=entrega['id'], limite=1)
                exigir(len(detalle['resultados']) <= 1, 'La herramienta no limitó la página solicitada.')
                documentos = await llamar('consultar_entrega', contrato_id=contrato, entrega_id=entrega['id'])
                fuente = next(d for d in documentos['resultados'] if d['tipo'] != 'zip')
                fragmentos = await llamar('consultar_entrega', contrato_id=contrato, entrega_id=entrega['id'],
                                           documento_id=fuente['id'], limite=12)
                exigir(all(len(f['texto']) <= 900 for f in fragmentos['resultados']), 'Un fragmento excede el límite.')
            diferencias = await llamar('comparar_entregas', contrato_id=contrato, entrega_anterior_id=entregas[0]['id'],
                                        entrega_actual_id=entregas[1]['id'])
            await llamar('consultar_observaciones', contrato_id=contrato, limite=12)
            for nombre, argumentos in (
                ('consultar_expediente', {'contrato_id': contrato + 1}),
                ('consultar_entrega', {'contrato_id': contrato, 'entrega_id': 2_147_483_647}),
                ('consultar_entrega', {'contrato_id': contrato, 'entrega_id': entregas[0]['id'], 'documento_id': 2_147_483_647}),
                ('buscar_contratos', {'limite': 100}),
                ('guardar_propuestas', {}),
            ):
                negativa = await sesion.call_tool(f'sigelc_{nombre}', argumentos)
                exigir(negativa.isError, f'{nombre} admitió una operación no autorizada o fuera de límites.')
            return {
                'herramientas': sorted(herramientas), 'contrato': ficha['numero'],
                'versiones_consultadas': [e['version'] for e in entregas],
                'estados_comparacion': [f['estado'] for f in diferencias['resultados']],
            }


async def probar(args):
    base = args.base_url.rstrip('/')
    url = urlsplit(base)
    exigir(url.netloc and not any((url.username, url.password, url.path, url.query, url.fragment)),
           'Indique una URL de base sin credenciales, ruta, parámetros ni fragmento.')
    exigir(url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')),
           'La prueba admite HTTP solo por loopback.')
    tls = ssl.create_default_context(cafile=str(args.ca)) if args.ca else True
    async with httpx.AsyncClient(verify=tls, follow_redirects=False, trust_env=False, timeout=20) as cliente:
        piloto = Piloto(cliente, base, args.callback)
        await piloto.descubrir()
        await piloto.verificar_perimetro()
        await piloto.registrar()
        await piloto.autorizar('sigelc-usuario-ficticio-inexistente', None)
        contrasena = os.environ.pop('SIGELC_MCP_PRUEBA_PASSWORD', '') or getpass('Contraseña de la cuenta ficticia: ')
        codigo = await piloto.autorizar(args.usuario, contrasena)
        del contrasena
        tokens = await piloto.intercambiar(codigo)
        async with httpx.AsyncClient(
            verify=tls, trust_env=False, timeout=20, headers={'Authorization': f"Bearer {tokens['access_token']}"},
        ) as autenticado:
            resultado = await recorrido_mcp(piloto, autenticado)
        actual = await piloto.renovar(tokens)
        token = actual['access_token']
        exigir((await piloto.comprobar_token(tokens['access_token'])).status_code == 401,
               'El token anterior a la renovación conserva el acceso.')
        await piloto.rechazar_renovacion(tokens['refresh_token'])
        exigir((await piloto.comprobar_token(token)).status_code == 200, 'El token renovado no permite iniciar MCP.')
        await piloto.revocar(token)
        exigir((await piloto.comprobar_token(token)).status_code == 401, 'El token revocado aún permite acceso.')
        await piloto.rechazar_renovacion(actual['refresh_token'])
        resultado.update(oauth='descubrimiento, PKCE, audiencia, consentimiento y renovación aprobados',
                         revocacion='aprobada', limites='paginación, longitud y techo de respuesta aprobados',
                         perimetro='acceso anónimo, otros orígenes, rutas internas y expedientes ajenos rechazados')
        if args.esperar_expiracion:
            # Obtain a new grant because access revocation can also invalidate its token family.
            contrasena = args.clave_expiracion or getpass('Contraseña ficticia para emitir un token de expiración: ')
            expiracion = await piloto.intercambiar(await piloto.autorizar(args.usuario, contrasena))
            segundos = expiracion.get('expires_in')
            exigir(isinstance(segundos, int) and 1 <= segundos <= 40,
                   'Para esta prueba, configure una vigencia de acceso entre 1 y 40 segundos.')
            anterior = await piloto.comprobar_token(expiracion['access_token'])
            exigir(anterior.status_code == 200, 'El token de prueba expiró antes de la primera llamada.')
            await asyncio.sleep(segundos + 1)
            exigir((await piloto.comprobar_token(expiracion['access_token'])).status_code == 401,
                   'Se aceptó un token después de su vencimiento.')
            await piloto.revocar(expiracion['refresh_token'], tipo='refresh_token')
            resultado['expiracion'] = 'aprobada'
        else:
            resultado['expiracion'] = 'ejecute también la variante con vigencia corta'
        if args.probar_frecuencia:
            await piloto.verificar_limite()
            resultado['limite_por_frecuencia'] = 'aprobado'
        return resultado


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pruebas independientes del MCP remoto de SIGELC')
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--ca', type=Path, help='Certificado PEM de confianza para una prueba HTTPS local.')
    parser.add_argument('--callback', default=CALLBACK, help='URI ya permitida por el servidor. Nunca se sigue el callback.')
    parser.add_argument('--usuario', default='abogado-demo')
    parser.add_argument('--esperar-expiracion', action='store_true')
    parser.add_argument('--probar-frecuencia', action='store_true',
                        help='Comprueba el 429 de registro, al final y en un servidor local de ensayo.')
    argumentos = parser.parse_args()
    argumentos.clave_expiracion = os.getenv('SIGELC_MCP_PRUEBA_PASSWORD') if argumentos.esperar_expiracion else None
    try:
        print(json.dumps(asyncio.run(probar(argumentos)), ensure_ascii=False))
    except (RuntimeError, httpx.HTTPError) as error:
        parser.exit(1, f'Prueba fallida. {error}\n')
