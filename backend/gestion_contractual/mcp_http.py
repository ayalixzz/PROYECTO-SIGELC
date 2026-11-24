"""HTTP boundaries for the single-process remote pilot."""

from asyncio import timeout
from collections import deque
from time import monotonic

from starlette.responses import JSONResponse


RUTAS = frozenset({
    '/mcp', '/authorize', '/token', '/register', '/revoke', '/oauth/consentir',
    '/.well-known/oauth-protected-resource/mcp', '/.well-known/oauth-authorization-server',
})
MAX_CUERPO = 64 * 1024
LIMITES = {
    ('*', '*'): 300,
    ('*', '/mcp'): 90,
    ('POST', '/oauth/consentir'): 8,
    ('POST', '/register'): 15,
    ('POST', '/token'): 50,
}


class LimitesHTTP:
    def __init__(self, app, *, hosts, origenes):
        self.app = app
        self.hosts = hosts
        self.origenes = origenes
        self.solicitudes = {clave: deque() for clave in LIMITES}

    def dentro_del_limite(self, metodo, ruta):
        ahora = monotonic()
        colas = []
        for clave in (('*', '*'), ('*', ruta), (metodo, ruta)):
            cola = self.solicitudes.get(clave)
            if cola is None:
                continue
            while cola and ahora - cola[0] >= 60:
                cola.popleft()
            if len(cola) >= LIMITES[clave]:
                return False
            colas.append(cola)
        for cola in colas:
            cola.append(ahora)
        return True

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def responder(mensaje):
            if mensaje['type'] == 'http.response.start':
                headers = [(k, v) for k, v in mensaje['headers'] if k.lower() != b'cache-control']
                headers += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff')]
                mensaje = {**mensaje, 'headers': headers}
            await send(mensaje)

        async def rechazar(status, error, **kwargs):
            await JSONResponse({'error': error}, status_code=status, **kwargs)(scope, receive, responder)

        cabeceras = scope['headers']
        hosts = [v.lower() for k, v in cabeceras if k == b'host']
        origenes = [v for k, v in cabeceras if k == b'origin']
        if len(hosts) != 1 or hosts[0] not in self.hosts or (
            origenes and (len(origenes) != 1 or origenes[0] not in self.origenes)
        ):
            await rechazar(403, 'host_or_origin_not_allowed')
            return
        ruta = scope['path']
        if ruta not in RUTAS or scope.get('raw_path', ruta.encode()) != ruta.encode():
            await rechazar(404, 'not_found')
            return
        if not self.dentro_del_limite(scope['method'], ruta):
            await rechazar(429, 'rate_limit', headers={'Retry-After': '60'})
            return
        if len(scope['query_string']) > 4096:
            await rechazar(414, 'query_too_large')
            return
        longitudes = [v for k, v in cabeceras if k == b'content-length']
        if len(longitudes) > 1 or any(not v.isdigit() or len(v) > 19 for v in longitudes):
            await rechazar(400, 'invalid_content_length')
            return
        if longitudes and int(longitudes[0]) > MAX_CUERPO:
            await rechazar(413, 'request_too_large')
            return
        cuerpo = bytearray()
        try:
            async with timeout(10):
                while True:
                    bloque = await receive()
                    if bloque['type'] == 'http.disconnect':
                        return
                    cuerpo.extend(bloque.get('body', b''))
                    if len(cuerpo) > MAX_CUERPO:
                        await rechazar(413, 'request_too_large')
                        return
                    if not bloque.get('more_body'):
                        break
        except TimeoutError:
            await rechazar(408, 'request_timeout')
            return

        async def leer_cuerpo():
            nonlocal consumido
            if consumido:
                return await receive()
            consumido = True
            return {'type': 'http.request', 'body': bytes(cuerpo), 'more_body': False}

        consumido = False
        await self.app(scope, leer_cuerpo, responder)
