"""Sirve SIGELC y, cuando se habilita, el conector MCP sobre el mismo HTTPS."""

import os

from django.conf import settings
from django.core.asgi import get_asgi_application
from django.db import connections


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings_hospedado')
django_app = get_asgi_application()

from gestion_contractual.mcp_transporte import combinar_aplicaciones, crear_aplicacion, verificar_entregas


def crear_aplicacion_hospedada():
    entregas = settings.SIGELC_MCP_ENTREGAS
    if not entregas:
        return django_app
    try:
        verificar_entregas(entregas)
    finally:
        connections.close_all()
    mcp = crear_aplicacion(
        url_publica=f'https://{settings.ALLOWED_HOSTS[0]}',
        puerto=443,
        entregas=entregas,
        permitir_propuestas=settings.SIGELC_MCP_PROPUESTAS,
        permitir_loopback=False,
    )
    return combinar_aplicaciones(django_app, mcp)


application = crear_aplicacion_hospedada()
