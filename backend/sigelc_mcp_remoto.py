"""Streamable HTTP entry point for the isolated SIGELC pilot."""

import argparse
import os
import re
from urllib.parse import urlsplit

import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_demo'
django.setup()

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn

from gestion_contractual.mcp_demo import NUMERO, expediente_ficticio, verificar_base
from gestion_contractual.mcp_http import LimitesHTTP
from gestion_contractual.mcp_oauth import ProveedorOAuth
from gestion_contractual.mcp_tools import crear_mcp
from gestion_contractual.models import Contrato


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
    candidatos = list(Contrato.objects.filter(numero_contrato=NUMERO).select_related('abogado_responsable')[:2])
    if len(candidatos) != 1:
        raise ValidationError('Se necesita un expediente ficticio único provisionado.')
    contrato = expediente_ficticio(candidatos[0].abogado_responsable)
    if contrato.revisiones_inteligentes.filter(pk__in=entregas).count() != len(entregas):
        raise ValidationError('Las entregas deben pertenecer al expediente ficticio.')


def crear_aplicacion(*, url_publica, puerto, entregas, redirect_uris=(), ttl_acceso=600,
                     permitir_propuestas=False):
    canonica = url_canonica(url_publica, puerto)
    proveedor = ProveedorOAuth(
        canonica, entregas, redirect_uris=redirect_uris, access_ttl=ttl_acceso,
        permitir_propuestas=permitir_propuestas,
    )
    hosts = {f'127.0.0.1:{puerto}', urlsplit(canonica).netloc}
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


class ServidorPiloto(uvicorn.Server):
    def __init__(self, config, senal_detener):
        super().__init__(config)
        self.senal_detener = senal_detener

    async def on_tick(self, counter):
        if self.senal_detener is not None and self.senal_detener.is_file():
            self.should_exit = True
        return await super().on_tick(counter)


def main(senal_detener=None):
    parser = argparse.ArgumentParser(description='Servidor SIGELC MCP HTTP para el piloto ficticio')
    parser.add_argument('--url-publica', required=True, help='URL base sin /mcp')
    parser.add_argument('--puerto', type=int, default=8765, choices=range(1024, 65536), metavar='1024..65535')
    parser.add_argument('--entrega', type=int, required=True, action='append', help='ID de una entrega ficticia autorizada')
    parser.add_argument('--redirect-uri', action='append', default=[], help='Callback OAuth adicional explícito')
    parser.add_argument('--ttl-acceso', type=int, default=600, choices=range(3, 601), metavar='3..600')
    parser.add_argument('--permitir-propuestas', action='store_true',
                        help='Permite devolver propuestas pendientes a solicitudes abiertas desde SIGELC')
    args = parser.parse_args()
    try:
        verificar_entregas(args.entrega)
        settings.SIGELC_CLAUDE_ENTREGAS = tuple(args.entrega) if args.permitir_propuestas else ()
        app = crear_aplicacion(url_publica=args.url_publica, puerto=args.puerto, entregas=args.entrega,
                              redirect_uris=args.redirect_uri, ttl_acceso=args.ttl_acceso,
                              permitir_propuestas=args.permitir_propuestas)
    except (ValidationError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    finally:
        connections.close_all()
    ServidorPiloto(uvicorn.Config(
        app, host='127.0.0.1', port=args.puerto, proxy_headers=False, access_log=False, server_header=False,
        limit_concurrency=24, log_level='warning', timeout_keep_alive=5, timeout_graceful_shutdown=5,
    ), senal_detener).run()


if __name__ == '__main__':
    main()
