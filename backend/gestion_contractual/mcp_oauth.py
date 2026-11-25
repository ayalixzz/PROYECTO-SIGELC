"""Short-lived OAuth grants for one fictional SIGELC dossier per pilot process."""

from dataclasses import dataclass
from hashlib import sha256
from html import escape
import logging
import re
from secrets import token_urlsafe
from threading import RLock
from time import time
from urllib.parse import urlsplit

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.utils.crypto import constant_time_compare
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthToken
from pydantic import AnyHttpUrl
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .mcp_demo import PERFIL_HOSPEDADO, expediente_ficticio, verificar_base
from .models import Auditoria


LOGGER = logging.getLogger('sigelc.mcp.oauth')
LECTURA = 'sigelc:read'
PROPUESTAS = 'sigelc:propuestas'
PERMISO_PROPUESTAS = 'gestion_contractual.change_documentocontrato'
CLAUDE_CALLBACK = 'https://claude.ai/api/mcp/auth_callback'
COOKIE_CSRF = 'sigelc_oauth_csrf'
CHALLENGE = re.compile(r'[a-zA-Z0-9_-]{43}')
VERIFIER = re.compile(r'[a-zA-Z0-9._~-]{43,128}')
CLIENT_TTL = 12 * 3600
PENDING_TTL = 300
CODE_TTL = 90
MAX_CLIENTS = 128
MAX_PENDING = 96
MAX_TOKENS = 128
CABECERAS = {'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer'}


@dataclass(frozen=True)
class Identidad:
    usuario: int
    autenticacion: str
    contrato: int
    version_contrato: int
    entregas: tuple[int, ...]

    def permiso(self):
        return {'usuario': self.usuario, 'contrato': self.contrato, 'version_contrato': self.version_contrato,
                'entregas': self.entregas, 'escritura': False}


@dataclass(frozen=True)
class Consentimiento:
    client_id: str
    solicitud: AuthorizationParams
    vence: float


@dataclass(frozen=True)
class FamiliaToken:
    identidad: Identidad
    access: AccessToken
    refresh: RefreshToken


def _huella(valor):
    return sha256(valor.encode()).digest()


def _falla(error, descripcion):
    return JSONResponse({'error': error, 'error_description': descripcion}, status_code=400, headers=CABECERAS)


def _es_formulario(request):
    return request.headers.get('content-type', '').split(';', 1)[0].strip().lower() == 'application/x-www-form-urlencoded'


def _identidad_vigente(identidad):
    verificar_base()
    usuario = get_user_model().objects.filter(pk=identidad.usuario).first()
    if not usuario or not constant_time_compare(usuario.get_session_auth_hash(), identidad.autenticacion):
        raise ValidationError('La autorización de la cuenta dejó de estar vigente.')
    contrato = expediente_ficticio(usuario)
    if contrato.pk != identidad.contrato or contrato.version_registro != identidad.version_contrato:
        raise ValidationError('El expediente cambió. Autorice nuevamente el acceso de lectura.')
    return usuario, contrato


def _auditar(usuario, contrato, accion, client_id, herramienta=None, *, alcances=(LECTURA,)):
    datos = {'cliente_oauth': client_id, 'alcance': ' '.join(alcances)}
    if herramienta:
        datos['herramienta'] = herramienta
    Auditoria.objects.create(
        usuario=usuario, accion=accion, entidad_afectada='Contrato', identificador_registro=str(contrato.pk),
        datos_nuevos=datos,
    )


class ProveedorOAuth:
    def __init__(self, issuer, entregas, *, redirect_uris=(), access_ttl=600, refresh_ttl=3600,
                 permitir_propuestas=False):
        publicado = AnyHttpUrl(issuer)
        if (publicado.path not in (None, '/') or publicado.username or publicado.password or
                publicado.query is not None or publicado.fragment is not None or
                (publicado.scheme != 'https' and publicado.host not in ('127.0.0.1', 'localhost', '[::1]'))):
            raise ValueError('Use un origen HTTPS sin ruta, salvo HTTP de desarrollo en loopback.')
        if not 1 <= access_ttl <= 900 or not access_ttl < refresh_ttl <= 14400:
            raise ValueError('La vigencia debe ser hasta 15 minutos por token y hasta cuatro horas por sesión.')
        if not 1 <= len(set(entregas)) <= 4 or not all(isinstance(e, int) and e > 0 for e in entregas):
            raise ValueError('Configure entre una y cuatro entregas ficticias.')
        self.issuer = str(publicado).rstrip('/')
        self.resource = self.issuer + '/mcp'
        self.secure = publicado.scheme == 'https'
        self.redirect_uris = frozenset(self._callback(uri) for uri in (CLAUDE_CALLBACK, *redirect_uris))
        self.access_ttl, self.refresh_ttl = access_ttl, refresh_ttl
        self.entregas = tuple(sorted(set(entregas)))
        self.alcances = (LECTURA, PROPUESTAS) if permitir_propuestas else (LECTURA,)
        self.lock = RLock()
        self.clientes = {}
        self.solicitudes = {}
        self.codigos = {}
        self.accesos = {}
        self.renovaciones = {}
        self._intentos = []

    @staticmethod
    def _callback(valor):
        url = AnyHttpUrl(valor)
        if (url.username or url.password or url.query is not None or url.fragment is not None or
                (url.scheme != 'https' and url.host not in ('localhost', '127.0.0.1', '[::1]'))):
            raise ValueError('Las redirecciones OAuth deben usar HTTPS o un callback de prueba en loopback.')
        return str(url)

    def auth_settings(self):
        return AuthSettings(
            issuer_url=AnyHttpUrl(self.issuer), resource_server_url=AnyHttpUrl(self.resource),
            required_scopes=[LECTURA], client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=list(self.alcances), default_scopes=list(self.alcances),
                client_secret_expiry_seconds=CLIENT_TTL,
            ), revocation_options=RevocationOptions(enabled=True),
        )

    def configure(self, app):
        """Install strict audience checks ahead of the SDK handlers and the operator login form."""
        auth = AuthorizationHandler(self)
        clientes = ClientAuthenticator(self)
        token = TokenHandler(self, clientes)

        async def autorizar(request):
            if request.method == 'POST' and not _es_formulario(request):
                return _falla('invalid_request', 'Use un formulario URL encoded.')
            datos = request.query_params if request.method == 'GET' else await request.form()
            if len(datos.multi_items()) != len(datos):
                return _falla('invalid_request', 'Hay parámetros repetidos.')
            if datos.get('resource') != self.resource:
                return _falla('invalid_target', 'El recurso debe coincidir con el endpoint MCP de SIGELC.')
            if datos.get('code_challenge_method') != 'S256':
                return _falla('invalid_request', 'PKCE S256 es obligatorio.')
            return await auth.handle(request)

        async def intercambiar(request):
            if not _es_formulario(request):
                return _falla('invalid_request', 'Use un formulario URL encoded.')
            datos = await request.form()
            if len(datos.multi_items()) != len(datos):
                return _falla('invalid_request', 'Hay parámetros repetidos.')
            if datos.get('resource') != self.resource:
                return _falla('invalid_target', 'El recurso debe coincidir con el endpoint MCP de SIGELC.')
            if datos.get('grant_type') == 'authorization_code':
                verificador = datos.get('code_verifier')
                if not isinstance(verificador, str) or not VERIFIER.fullmatch(verificador):
                    return _falla('invalid_grant', 'El verificador PKCE es inválido.')
            return await token.handle(request)

        async def metadatos(request):
            config = self.auth_settings()
            documento = build_metadata(config.issuer_url, None, config.client_registration_options,
                                       config.revocation_options).model_dump(mode='json', exclude_none=True)
            documento['token_endpoint_auth_methods_supported'].append('none')
            documento['revocation_endpoint_auth_methods_supported'].append('none')
            return JSONResponse(documento, headers=CABECERAS)

        async def revocar(request):
            datos = await request.form()
            valor = datos.get('token')
            if (len(datos.multi_items()) != len(datos) or not isinstance(valor, str)
                    or not 1 <= len(valor) <= 256 or datos.get('token_type_hint') not in (
                        None, '', 'access_token', 'refresh_token',
                    )):
                return _falla('invalid_request', 'La solicitud de revocación es inválida.')
            try:
                cliente = await clientes.authenticate_request(request)
            except AuthenticationError:
                return JSONResponse({'error': 'invalid_client'}, status_code=401, headers=CABECERAS)
            with self.lock:
                clave = _huella(valor)
                familia = self.accesos.get(clave) or self.renovaciones.get(clave)
            if familia and familia.access.client_id == cliente.client_id:
                await self.revoke_token(familia.access)
            return JSONResponse({}, headers=CABECERAS)

        reemplazos = [
            Route('/authorize', autorizar, methods=['GET', 'POST']),
            Route('/token', intercambiar, methods=['POST']),
            Route('/revoke', revocar, methods=['POST']),
            Route('/.well-known/oauth-authorization-server', metadatos, methods=['GET']),
            Route('/oauth/consentir', self.consentir, methods=['GET', 'POST']),
            *create_protected_resource_routes(
                resource_url=AnyHttpUrl(self.resource), authorization_servers=[AnyHttpUrl(self.issuer)],
                scopes_supported=list(self.alcances),
            ),
        ]
        rutas = {ruta.path for ruta in reemplazos}
        app.router.routes[:] = [*reemplazos, *(r for r in app.router.routes if getattr(r, 'path', None) not in rutas)]

    def _limpiar(self, ahora):
        for clave, (_, vence) in list(self.clientes.items()):
            if vence <= ahora:
                self.clientes.pop(clave, None)
        for clave, pendiente in list(self.solicitudes.items()):
            if pendiente.vence <= ahora:
                self.solicitudes.pop(clave, None)
        for clave, (codigo, _) in list(self.codigos.items()):
            if codigo.expires_at <= ahora:
                self.codigos.pop(clave, None)
        for familia in tuple(self.renovaciones.values()):
            if familia.refresh.expires_at <= ahora:
                self._eliminar_familia(familia)

    async def register_client(self, cliente):
        if (not cliente.redirect_uris or len(cliente.redirect_uris) > 4 or
                any(str(uri) not in self.redirect_uris for uri in cliente.redirect_uris)):
            raise RegistrationError('invalid_redirect_uri', 'El callback no está aprobado para este piloto.')
        alcances = (cliente.scope or '').split()
        if (cliente.token_endpoint_auth_method not in ('none', 'client_secret_post', 'client_secret_basic') or
                set(cliente.grant_types) != {'authorization_code', 'refresh_token'} or
                set(cliente.response_types) != {'code'} or not self._alcance_valido(alcances)):
            raise RegistrationError('invalid_client_metadata', 'El registro contiene un alcance que SIGELC no ofrece.')
        ahora = time()
        with self.lock:
            self._limpiar(ahora)
            if len(self.clientes) >= MAX_CLIENTS:
                raise RegistrationError('invalid_client_metadata', 'Capacidad temporal de clientes agotada.')
            self.clientes[cliente.client_id] = (cliente.model_copy(deep=True), ahora + CLIENT_TTL)

    async def get_client(self, client_id):
        with self.lock:
            cliente = self.clientes.get(client_id)
            return cliente[0].model_copy(deep=True) if cliente and cliente[1] > time() else None

    async def authorize(self, cliente, params):
        if (params.resource != self.resource or not params.state or len(params.state) > 512 or
                not CHALLENGE.fullmatch(params.code_challenge) or str(params.redirect_uri) not in self.redirect_uris):
            raise AuthorizeError('invalid_request', 'Recurso, estado, callback o reto OAuth inválido.')
        alcances = params.scopes
        if alcances is None:
            # Claude puede omitir el parámetro y usar los alcances de su registro.
            hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
            alcances = (cliente.scope or LECTURA).split() if hospedado and PROPUESTAS in self.alcances else [LECTURA]
        if not self._alcance_valido(alcances):
            raise AuthorizeError('invalid_scope', 'El alcance solicitado no está disponible en este piloto.')
        params = params.model_copy(update={'scopes': [a for a in self.alcances if a in alcances]})
        ahora = time()
        identificador = token_urlsafe(32)
        with self.lock:
            self._limpiar(ahora)
            if len(self.solicitudes) >= MAX_PENDING:
                raise AuthorizeError('temporarily_unavailable', 'Hay demasiadas solicitudes pendientes.')
            self.solicitudes[_huella(identificador)] = Consentimiento(cliente.client_id, params, ahora + PENDING_TTL)
        return construct_redirect_uri(self.issuer + '/oauth/consentir', solicitud=identificador)

    def _alcance_valido(self, alcances):
        return (LECTURA in alcances and len(alcances) == len(set(alcances)) and
                set(alcances).issubset(self.alcances))

    def _csrf(self, solicitud):
        from django.core.signing import TimestampSigner

        return TimestampSigner(salt='sigelc.oauth.consentimiento').sign(solicitud + '.' + token_urlsafe(24))

    def _csrf_valido(self, solicitud, valor, cookie):
        from django.core.signing import BadSignature, TimestampSigner

        if (not isinstance(valor, str) or not valor or not cookie or len(valor) > 255 or
                not constant_time_compare(valor, cookie)):
            return False
        try:
            contenido = TimestampSigner(salt='sigelc.oauth.consentimiento').unsign(valor, max_age=PENDING_TTL)
        except BadSignature:
            return False
        return contenido.startswith(solicitud + '.')

    async def consentir(self, request):
        if request.method == 'POST' and not _es_formulario(request):
            return _falla('invalid_request', 'Use un formulario URL encoded.')
        datos = request.query_params if request.method == 'GET' else await request.form()
        solicitud = datos.get('solicitud')
        if not isinstance(solicitud, str) or not CHALLENGE.fullmatch(solicitud) or len(datos.multi_items()) != len(datos):
            return _falla('invalid_request', 'La solicitud de autorización es inválida.')
        clave = _huella(solicitud)
        ahora = time()
        with self.lock:
            pendiente = self.solicitudes.get(clave)
        if not pendiente or pendiente.vence <= ahora:
            return _falla('invalid_request', 'La solicitud de autorización expiró o ya se usó.')
        alcances = pendiente.solicitud.scopes or [LECTURA]
        permite_propuestas = PROPUESTAS in alcances
        if request.method == 'GET':
            csrf = self._csrf(solicitud)
            destino = urlsplit(str(pendiente.solicitud.redirect_uri))
            csp = ("default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self' "
                   f'{destino.scheme}://{destino.netloc}')
            aviso_local = (
                '<p>El callback es local. Continúe solamente si inició la prueba en este equipo.</p>'
                if destino.hostname in ('localhost', '127.0.0.1', '::1') else ''
            )
            consentimiento_propuestas = (
                '<p><label><input name="propuestas" type="checkbox" value="si" required> '
                'Autorizo devolver propuestas pendientes a solicitudes de análisis que yo abra en SIGELC.'
                '</label></p>' if permite_propuestas else ''
            )
            formulario = HTMLResponse(
                '<!doctype html><html lang="es"><meta charset="utf-8"><title>Autorizar SIGELC</title>'
                '<main><h1>Autorizar la lectura de la demo de SIGELC</h1>'
                '<p>El permiso cubre el expediente ficticio SIGELC-DEMO-001 y las entregas autorizadas. '
                'El cliente OAuth podrá recibir fragmentos y usarlos en sus servicios.</p>'
                f'<p>El cliente recibirá la autorización en <strong>{escape(destino.netloc)}</strong>.</p>'
                f'{aviso_local}'
                '<form method="post" action="/oauth/consentir">'
                f'<input type="hidden" name="solicitud" value="{escape(solicitud, quote=True)}">'
                f'<input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">'
                '<p><label>Usuario SIGELC <input name="usuario" maxlength="150" autocomplete="username" required></label></p>'
                '<p><label>Contraseña SIGELC <input name="contrasena" type="password" maxlength="256" '
                'autocomplete="current-password" required></label></p>'
                '<p><label><input name="consentir" type="checkbox" value="si" required> '
                'Confirmo el uso de datos ficticios y autorizo la lectura.</label></p>'
                f'{consentimiento_propuestas}'
                '<button>Autorizar</button></form></main></html>',
                # A native form POST with no-referrer sends Origin: null.
                headers={**CABECERAS, 'Referrer-Policy': 'same-origin',
                         'Content-Security-Policy': csp, 'X-Frame-Options': 'DENY'},
            )
            formulario.set_cookie(COOKIE_CSRF, csrf, max_age=PENDING_TTL, path='/oauth/consentir',
                                   httponly=True, samesite='strict', secure=self.secure)
            return formulario

        if (request.headers.get('origin') != self.issuer or datos.get('consentir') != 'si' or
                (permite_propuestas and datos.get('propuestas') != 'si') or
                not self._csrf_valido(solicitud, datos.get('csrf'), request.cookies.get(COOKIE_CSRF))):
            return _falla('invalid_request', 'La confirmación y su origen deben ser válidos.')
        nombre, password = datos.get('usuario'), datos.get('contrasena')
        if not isinstance(nombre, str) or not isinstance(password, str) or not 1 <= len(nombre) <= 150 or not 1 <= len(password) <= 256:
            return _falla('invalid_request', 'Las credenciales de SIGELC son inválidas.')
        with self.lock:
            self._intentos = [t for t in self._intentos if ahora - t < 60]
            if len(self._intentos) >= 8:
                return JSONResponse({'error': 'temporarily_unavailable'}, status_code=429, headers=CABECERAS)
            if self.solicitudes.pop(clave, None) is None:
                return _falla('invalid_request', 'La solicitud ya se usó.')
            self._intentos.append(ahora)

        try:
            identidad = await sync_to_async(self._autenticar)(nombre, password, pendiente.client_id, alcances)
        except ValidationError:
            LOGGER.warning('Acceso OAuth SIGELC rechazado.')
            return JSONResponse({'error': 'access_denied'}, status_code=403, headers=CABECERAS)
        codigo = AuthorizationCode(
            code=token_urlsafe(32), client_id=pendiente.client_id, scopes=alcances,
            expires_at=time() + CODE_TTL, code_challenge=pendiente.solicitud.code_challenge,
            redirect_uri=pendiente.solicitud.redirect_uri,
            redirect_uri_provided_explicitly=pendiente.solicitud.redirect_uri_provided_explicitly,
            resource=self.resource, subject=str(identidad.usuario),
        )
        with self.lock:
            self._limpiar(time())
            if len(self.codigos) >= MAX_PENDING:
                return _falla('temporarily_unavailable', 'Capacidad temporal agotada.')
            self.codigos[_huella(codigo.code)] = (codigo, identidad)
        respuesta = RedirectResponse(
            construct_redirect_uri(str(codigo.redirect_uri), code=codigo.code, state=pendiente.solicitud.state),
            status_code=303, headers=CABECERAS,
        )
        respuesta.delete_cookie(COOKIE_CSRF, path='/oauth/consentir', httponly=True,
                                  samesite='strict', secure=self.secure)
        return respuesta

    def _autenticar(self, nombre, password, cliente, alcances):
        close_old_connections()
        try:
            verificar_base()
            usuario = authenticate(username=nombre, password=password)
            if usuario is None:
                raise ValidationError('Credenciales inválidas.')
            contrato = expediente_ficticio(usuario)
            if PROPUESTAS in alcances and not usuario.has_perm(PERMISO_PROPUESTAS):
                raise ValidationError('El abogado carece de permiso para devolver propuestas.')
            disponibles = set(contrato.revisiones_inteligentes.filter(pk__in=self.entregas).values_list('pk', flat=True))
            if disponibles != set(self.entregas):
                raise ValidationError('Entregas ficticias fuera del ámbito del expediente.')
            identidad = Identidad(
                usuario.pk, usuario.get_session_auth_hash(), contrato.pk, contrato.version_registro, self.entregas,
            )
            _auditar(usuario, contrato, 'mcp_oauth_autorizar', cliente, alcances=alcances)
            return identidad
        finally:
            close_old_connections()

    async def load_authorization_code(self, cliente, authorization_code):
        with self.lock:
            fila = self.codigos.get(_huella(authorization_code))
            if fila and fila[0].client_id == cliente.client_id and fila[0].expires_at > time():
                return fila[0].model_copy(deep=True)
        return None

    async def exchange_authorization_code(self, cliente, codigo):
        with self.lock:
            ahora = int(time())
            self._limpiar(ahora)
            fila = self.codigos.pop(_huella(codigo.code), None)
            if (not fila or fila[0].client_id != cliente.client_id or fila[0].resource != self.resource or
                    fila[0].expires_at <= time()):
                raise TokenError('invalid_grant', 'El código ya no es válido.')
            return self._emitir(cliente.client_id, fila[1], fila[0].scopes, ahora, ahora + self.refresh_ttl)

    def _emitir(self, client_id, identidad, alcances, ahora, vence):
        if len(self.renovaciones) >= MAX_TOKENS or vence <= ahora:
            raise TokenError('invalid_grant', 'La sesión expiró o la capacidad temporal está agotada.')
        access = AccessToken(
            token=token_urlsafe(32), client_id=client_id, subject=str(identidad.usuario), resource=self.resource,
            scopes=alcances, expires_at=min(vence, ahora + self.access_ttl),
        )
        refresh = RefreshToken(token=token_urlsafe(32), client_id=client_id, subject=str(identidad.usuario),
                               scopes=alcances, expires_at=vence)
        familia = FamiliaToken(identidad, access, refresh)
        self.accesos[_huella(access.token)] = familia
        self.renovaciones[_huella(refresh.token)] = familia
        return OAuthToken(access_token=access.token, refresh_token=refresh.token,
                          expires_in=access.expires_at - ahora, scope=' '.join(alcances), token_type='Bearer')

    async def load_refresh_token(self, cliente, refresh_token):
        with self.lock:
            familia = self.renovaciones.get(_huella(refresh_token))
            if familia and familia.refresh.expires_at > time() and familia.refresh.client_id == cliente.client_id:
                return familia.refresh.model_copy(deep=True)
        return None

    async def exchange_refresh_token(self, cliente, refresh_token, scopes):
        with self.lock:
            familia = self.renovaciones.get(_huella(refresh_token.token))
        if (not familia or familia.refresh.client_id != cliente.client_id or
                familia.refresh.expires_at <= time() or not self._alcance_valido(scopes) or
                not set(scopes).issubset(familia.refresh.scopes) or not await self._vigente(familia)):
            raise TokenError('invalid_grant', 'La sesión de lectura ya no es válida.')
        with self.lock:
            if self.renovaciones.get(_huella(refresh_token.token)) is not familia:
                raise TokenError('invalid_grant', 'La sesión de lectura cambió.')
            self._eliminar_familia(familia)
            alcances = [a for a in self.alcances if a in scopes]
            return self._emitir(cliente.client_id, familia.identidad, alcances, int(time()), familia.refresh.expires_at)

    def _familia_acceso(self, token):
        with self.lock:
            familia = self.accesos.get(_huella(token))
            if (familia and familia.access.expires_at > time() and familia.access.resource == self.resource and
                    self._alcance_valido(familia.access.scopes)):
                return familia
        return None

    @staticmethod
    async def _vigente(familia):
        def comprobar():
            close_old_connections()
            try:
                _identidad_vigente(familia.identidad)
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()
        return await sync_to_async(comprobar)()

    async def load_access_token(self, token):
        familia = self._familia_acceso(token)
        if familia and await self._vigente(familia) and self._familia_acceso(token) is familia:
            return familia.access.model_copy(deep=True)
        return None

    def _eliminar_familia(self, familia):
        self.accesos.pop(_huella(familia.access.token), None)
        self.renovaciones.pop(_huella(familia.refresh.token), None)

    async def revoke_token(self, token):
        with self.lock:
            clave = _huella(token.token)
            familia = self.accesos.get(clave) or self.renovaciones.get(clave)
            if familia:
                self._eliminar_familia(familia)
        if familia:
            def registrar():
                close_old_connections()
                try:
                    usuario, contrato = _identidad_vigente(familia.identidad)
                    _auditar(usuario, contrato, 'mcp_oauth_revocar', token.client_id, alcances=familia.access.scopes)
                except ValidationError:
                    LOGGER.info('Permiso OAuth de una identidad inactiva revocado.')
                finally:
                    close_old_connections()
            await sync_to_async(registrar)()

    def acceso_actual(self, herramienta=None):
        token = get_access_token()
        familia = self._familia_acceso(token.token) if token else None
        if not familia:
            raise ValidationError('Se requiere una autorización remota de solo lectura vigente.')
        usuario, contrato = _identidad_vigente(familia.identidad)
        propuestas = (PROPUESTAS in familia.access.scopes and PROPUESTAS in self.alcances and
                      usuario.has_perm(PERMISO_PROPUESTAS))
        requiere_propuestas = herramienta == 'sigelc_entregar_propuestas'
        if requiere_propuestas and not propuestas:
            raise ValidationError('Se requiere una autorización vigente para devolver propuestas.')
        if self._familia_acceso(token.token) is not familia:
            raise ValidationError('El permiso de lectura se revocó.')
        accion = 'mcp_oauth_entregar_propuestas' if requiere_propuestas else 'mcp_oauth_consultar'
        _auditar(usuario, contrato, accion, familia.access.client_id, herramienta,
                 alcances=familia.access.scopes)
        return usuario, contrato, {**familia.identidad.permiso(), 'propuestas': propuestas}
