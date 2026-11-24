"""Conectores HTTP optativos para OpenAI y Anthropic.

La autorización por revisión y la validación de evidencias viven en
revision_analisis. Este módulo no conoce expedientes ni guarda credenciales.
"""

import json
import os
import re
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .revision_analisis import ESQUEMA_IA, ProveedorAutorizado


OPENAI = 'https://api.openai.com/v1/responses'
ANTHROPIC = 'https://api.anthropic.com/v1/messages'
TIEMPO_ESPERA = 30
MAX_RESPUESTA = 192_000
MAX_SOLICITUD = 112_000
MAX_TOKENS = 4096
MODELO_VALIDO = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
CLAVE_VALIDA = re.compile(r'[!-~]{1,512}\Z')


class ErrorProveedor(RuntimeError):
    """Error genérico cuya cadena nunca contiene datos HTTP o credenciales."""


class _SinRedirecciones(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _objeto_sin_duplicados(pares):
    objeto = {}
    for clave, valor in pares:
        if clave in objeto:
            raise ValueError('Propiedad JSON duplicada.')
        objeto[clave] = valor
    return objeto


def _json_objeto(contenido):
    def constante_invalida(_):
        raise ValueError('Constante JSON no admisible.')

    try:
        resultado = json.loads(
            contenido, object_pairs_hook=_objeto_sin_duplicados,
            parse_constant=constante_invalida,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ErrorProveedor('La respuesta del proveedor no contiene JSON válido.') from None
    if type(resultado) is not dict:
        raise ErrorProveedor('La respuesta del proveedor no contiene un objeto JSON.')
    return resultado


def _enviar(url, headers, payload):
    if url not in (OPENAI, ANTHROPIC):
        raise ErrorProveedor('Destino de proveedor no admitido.')
    cuerpo = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(cuerpo) > MAX_SOLICITUD:
        raise ErrorProveedor('La solicitud supera el límite del conector.')
    solicitud = Request(
        url, data=cuerpo, method='POST',
        headers={'Accept': 'application/json', 'Content-Type': 'application/json', **headers},
    )
    cliente = build_opener(ProxyHandler({}), _SinRedirecciones())
    fin = monotonic() + TIEMPO_ESPERA
    try:
        with cliente.open(solicitud, timeout=TIEMPO_ESPERA) as respuesta:
            if respuesta.status != 200 or respuesta.headers.get_content_type() != 'application/json':
                raise ErrorProveedor('El proveedor devolvió un formato inesperado.')
            fragmentos = bytearray()
            while monotonic() < fin:
                parte = respuesta.read1(min(16384, MAX_RESPUESTA + 1 - len(fragmentos)))
                if not parte:
                    return _json_objeto(fragmentos)
                fragmentos.extend(parte)
                if len(fragmentos) > MAX_RESPUESTA:
                    raise ErrorProveedor('La respuesta del proveedor supera el límite.')
            raise ErrorProveedor('La respuesta del proveedor excedió el tiempo permitido.')
    except HTTPError as error:
        # No se lee ni conserva el cuerpo del error remoto.
        error.close()
        raise ErrorProveedor('El proveedor rechazó la solicitud. Revise autorización y modelo.') from None
    except (URLError, OSError):
        raise ErrorProveedor('No se pudo completar la conexión HTTPS con el proveedor.') from None


def _esquema_remoto(esquema):
    """Proyecta el esquema común. Tamaño, mínimos y evidencias se comprueban en SIGELC."""
    salida = {
        campo: esquema[campo] for campo in ('type', 'required', 'additionalProperties', 'enum')
        if campo in esquema
    }
    if 'const' in esquema:
        salida.update(type='string', enum=[esquema['const']])
    if 'enum' in salida:
        salida['type'] = 'string'
    if 'properties' in esquema:
        salida['properties'] = {
            campo: _esquema_remoto(valor) for campo, valor in esquema['properties'].items()
        }
    if 'items' in esquema:
        salida['items'] = _esquema_remoto(esquema['items'])
    return salida


def _entrada(solicitud):
    if not solicitud.get('fragmentos'):
        raise ErrorProveedor('La revisión no dispone de fragmentos analizables.')
    return json.dumps({
        campo: solicitud[campo] for campo in
        ('contexto_minimo', 'reglas', 'referencias_juridicas', 'fragmentos')
    }, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _texto_unico(partes, tipo):
    if not isinstance(partes, list) or len(partes) != 1:
        raise ErrorProveedor('El proveedor no entregó una única respuesta estructurada.')
    parte = partes[0]
    if not isinstance(parte, dict) or parte.get('type') != tipo or not isinstance(parte.get('text'), str):
        raise ErrorProveedor('El proveedor no entregó texto estructurado.')
    return _json_objeto(parte['text'])


def _openai(solicitud, modelo, clave, transporte):
    respuesta = transporte(OPENAI, {'Authorization': f'Bearer {clave}'}, {
        'model': modelo,
        'store': False,
        'max_output_tokens': MAX_TOKENS,
        'instructions': solicitud['instrucciones'],
        'input': [{'role': 'user', 'content': _entrada(solicitud)}],
        'text': {'format': {
            'type': 'json_schema',
            'name': 'revision_documental_sigelc',
            'strict': True,
            'schema': _esquema_remoto(ESQUEMA_IA),
        }},
    })
    if respuesta.get('status') != 'completed' or respuesta.get('error') or respuesta.get('incomplete_details'):
        raise ErrorProveedor('La respuesta de OpenAI quedó incompleta.')
    elementos = respuesta.get('output')
    if not isinstance(elementos, list) or any(
        not isinstance(item, dict) or item.get('type') not in ('reasoning', 'message') for item in elementos
    ):
        raise ErrorProveedor('OpenAI devolvió elementos de respuesta inesperados.')
    mensajes = [item for item in elementos if item.get('type') == 'message']
    if len(mensajes) != 1 or mensajes[0].get('role') != 'assistant' or mensajes[0].get('status') != 'completed':
        raise ErrorProveedor('OpenAI no devolvió un mensaje de asistente completo.')
    return _texto_unico(mensajes[0].get('content'), 'output_text')


def _anthropic(solicitud, modelo, clave, transporte):
    respuesta = transporte(ANTHROPIC, {'x-api-key': clave, 'anthropic-version': '2023-06-01'}, {
        'model': modelo,
        'max_tokens': MAX_TOKENS,
        'system': solicitud['instrucciones'],
        'messages': [{'role': 'user', 'content': _entrada(solicitud)}],
        'output_config': {'format': {
            'type': 'json_schema',
            'schema': _esquema_remoto(ESQUEMA_IA),
        }},
    })
    if respuesta.get('type') != 'message' or respuesta.get('role') != 'assistant' or respuesta.get('stop_reason') != 'end_turn':
        raise ErrorProveedor('Claude no devolvió un mensaje de asistente completo.')
    return _texto_unico(respuesta.get('content'), 'text')


_PROVEEDORES = {
    'openai': (_openai, 'OpenAI', 'OPENAI'),
    'anthropic': (_anthropic, 'Anthropic Claude', 'ANTHROPIC'),
}


def crear_proveedor(nombre, *, modelo, clave, transporte=_enviar):
    """Construye el adaptador sin comprobar credenciales ni realizar una conexión."""
    if nombre not in _PROVEEDORES:
        raise ValueError('Proveedor de IA no admitido.')
    if not isinstance(modelo, str) or not MODELO_VALIDO.fullmatch(modelo):
        raise ValueError('Indique un identificador de modelo válido.')
    if not isinstance(clave, str) or not CLAVE_VALIDA.fullmatch(clave):
        raise ValueError('La clave del proveedor falta o tiene caracteres no admitidos.')
    ejecutar, etiqueta, _ = _PROVEEDORES[nombre]

    def ejecutar_revision(solicitud):
        return ejecutar(solicitud, modelo, clave, transporte)

    return ProveedorAutorizado(ejecutor=ejecutar_revision, nombre=etiqueta, version=modelo)


def proveedor_desde_entorno(nombre, *, modelo=None):
    """Variables locales opcionales. Ninguna clave o modelo se presupone."""
    if nombre not in _PROVEEDORES:
        raise ValueError('Proveedor de IA no admitido.')
    _, _, prefijo = _PROVEEDORES[nombre]
    clave = os.environ.get(f'{prefijo}_API_KEY', '')
    modelo = modelo or os.environ.get(f'{prefijo}_MODEL', '')
    return crear_proveedor(nombre, modelo=modelo, clave=clave) if clave and modelo else None
