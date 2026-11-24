"""Transporte MCP reutilizable para la demo local y la demo hospedada."""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_demo import (
    NUMERO, PERFIL_HOSPEDADO, expediente_ficticio, verificar_base, verificar_entregas_hospedadas,
)
from .mcp_http import LimitesHTTP, RUTAS
from .mcp_oauth import ProveedorOAuth
from .mcp_tools import crear_mcp
from .models import Contrato


def url_canonica(valor, puerto):
    url = urlsplit(valor)
    host = url.hostname or ''
    if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host, flags=re.ASCII)
            or url.username is not None or url.path not in ('', '/') or any(x in valor for x in '?#\\')
            or url.scheme not in ('http', 'https') or not re.fullmatch(r'[a-z0-9.:-]+', url.netloc)):
        raise ValueError('Use una URL base sin credenciales, ruta, consulta o fragmento.')
    if url.scheme == 'http' and (host != '127.0.0.1' or url.port != puerto):
        raise ValueError('HTTP se admite solo para el servidor de prueba en 127.0.0.1 y su puerto local.')
    if url.scheme == 'https' and host != '127.0.0.1' and ('.' not in host or url.port not in (443, None)):
        raise ValueError('Use un dominio HTTPS público en el puerto estándar 443.')
    default = 443 if url.scheme == 'https' else 80
    autoridad = host if url.port in (None, default) else f'{host}:{url.port}'
    return f'{url.scheme}://{autoridad}'


def verificar_entregas(entregas):
    verificar_base()
    if settings.DEBUG or not 1 <= len(entregas) <= 4 or len(entregas) != len(set(entregas)):
        raise ValidationError('Seleccione de una a cuatro entregas ficticias distintas, con DEBUG desactivado.')
    hospedado = settings.SETTINGS_MODULE == PERFIL_HOSPEDADO
    candidatos = Contrato.objects.filter(numero_contrato=NUMERO)
    if hospedado:
        candidatos = candidatos.filter(revisiones_inteligentes__pk=entregas[0])
    candidatos = list(candidatos.select_related('abogado_responsable')[:2])
    if len(candidatos) != 1:
        raise ValidationError('Se necesita un expediente ficticio único provisionado.')
    contrato = expediente_ficticio(candidatos[0].abogado_responsable)
    if hospedado:
        verificar_entregas_hospedadas(contrato, entregas)
    elif contrato.revisiones_inteligentes.filter(pk__in=entregas).count() != len(entregas):
        raise ValidationError('Las entregas deben pertenecer al expediente ficticio.')


def combinar_aplicaciones(django, mcp):
    async def atender(scope, receive, send):
        tipo = scope['type']
        ruta_mcp = tipo == 'http' and scope['path'] in RUTAS
        if tipo == 'lifespan' or ruta_mcp:
            await mcp(scope, receive, send)
        else:
            await django(scope, receive, send)

    return atender


def crear_aplicacion(*, url_publica, puerto, entregas, redirect_uris=(), ttl_acceso=600,
                     permitir_propuestas=False, permitir_loopback=True):
    canonica = url_canonica(url_publica, puerto)
    proveedor = ProveedorOAuth(
        canonica, entregas, redirect_uris=redirect_uris, access_ttl=ttl_acceso,
        permitir_propuestas=permitir_propuestas,
    )
    hosts = {urlsplit(canonica).netloc}
    if permitir_loopback:
        hosts.add(f'127.0.0.1:{puerto}')
    origenes = {canonica, 'https://claude.ai'}
    servidor = crear_mcp(
        acceso=proveedor.acceso_actual, recibir_propuestas=permitir_propuestas,
        max_resultado=24 * 1024, streamable_http_path='/mcp',
        stateless_http=True, json_response=True, auth_server_provider=proveedor,
        auth=proveedor.auth_settings(),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=sorted(hosts), allowed_origins=sorted(origenes),
        ),
    )
    app = servidor.streamable_http_app()
    app.router.redirect_slashes = False
    proveedor.configure(app)
    return LimitesHTTP(app, hosts={h.encode() for h in hosts}, origenes={o.encode() for o in origenes})
