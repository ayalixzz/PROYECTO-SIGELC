"""Streamable HTTP entry point for the isolated SIGELC pilot."""

import argparse
import os

import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_demo'
django.setup()

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections
import uvicorn

from gestion_contractual.mcp_transporte import crear_aplicacion, url_canonica, verificar_entregas


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
