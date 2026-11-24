from email.message import Message
from io import BytesIO
import json
import os
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase

from gestion_contractual.revision_analisis import ESQUEMA, analizar
from gestion_contractual.revision_proveedores import (
    ANTHROPIC, OPENAI, ErrorProveedor, _SinRedirecciones, _enviar,
    crear_proveedor, proveedor_desde_entorno,
)


DOCUMENTOS = [{
    'id': 1, 'nombre': 'ficticio.docx', 'clasificacion': 'documento_contractual',
    'fragmentos': [{'ubicacion': 'Párrafo 1', 'texto': 'Contrato: DEMO-01'}],
}]
CONTEXTO = {'envio_autorizado': True, 'documentos_autorizados': [1]}
CLAVE_ENSAYO = 'CLAVE-FICTICIA-EXCLUSIVA-DE-PRUEBA'
RESPUESTA = {'esquema': ESQUEMA, 'hallazgos': []}


def openai(texto, *, status='completed', tipo='output_text'):
    return {
        'status': status,
        'output': [{'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'content': [{'type': tipo, 'text': texto}]}],
    }


def anthropic(texto, *, final='end_turn', tipo='text'):
    return {
        'type': 'message', 'role': 'assistant', 'stop_reason': final,
        'content': [{'type': tipo, 'text': texto}],
    }


class ProveedoresRevisionTest(SimpleTestCase):
    def test_openai_usa_responses_esquema_estricto_y_no_guarda_la_respuesta(self):
        def transporte(url, cabeceras, cuerpo):
            self.assertEqual(url, OPENAI)
            self.assertEqual(cabeceras['Authorization'], f'Bearer {CLAVE_ENSAYO}')
            self.assertEqual(cuerpo['model'], 'modelo-openai-configurado')
            self.assertIs(cuerpo['store'], False)
            self.assertNotIn('tools', cuerpo)
            self.assertNotIn(CLAVE_ENSAYO, json.dumps(cuerpo))
            formato = cuerpo['text']['format']
            self.assertIs(formato['strict'], True)
            self.assertEqual(formato['type'], 'json_schema')
            self.assertEqual(formato['schema']['properties']['esquema']['enum'], [ESQUEMA])
            entrada = json.loads(cuerpo['input'][0]['content'])
            self.assertEqual(entrada['fragmentos'][0]['texto'], 'Contrato: DEMO-01')
            self.assertIn('no confiable', cuerpo['instructions'])
            return openai(json.dumps(RESPUESTA))

        proveedor = crear_proveedor('openai', modelo='modelo-openai-configurado', clave=CLAVE_ENSAYO, transporte=transporte)
        self.assertNotIn(CLAVE_ENSAYO, repr(proveedor))
        self.assertEqual(analizar(DOCUMENTOS, CONTEXTO, proveedor)['ia']['estado'], 'disponible')

    def test_anthropic_usa_messages_con_salida_estructurada(self):
        def transporte(url, cabeceras, cuerpo):
            self.assertEqual(url, ANTHROPIC)
            self.assertEqual(cabeceras, {'x-api-key': CLAVE_ENSAYO, 'anthropic-version': '2023-06-01'})
            self.assertEqual(cuerpo['model'], 'claude-configurado')
            self.assertIn('no confiable', cuerpo['system'])
            self.assertNotIn('tools', cuerpo)
            self.assertNotIn(CLAVE_ENSAYO, json.dumps(cuerpo))
            formato = cuerpo['output_config']['format']
            self.assertEqual(formato['type'], 'json_schema')
            self.assertFalse(formato['schema']['additionalProperties'])
            esquema_hallazgo = formato['schema']['properties']['hallazgos']['items']
            self.assertEqual(esquema_hallazgo['properties']['gravedad']['type'], 'string')
            self.assertNotIn('maxLength', esquema_hallazgo['properties']['descripcion'])
            return anthropic(json.dumps(RESPUESTA))

        proveedor = crear_proveedor('anthropic', modelo='claude-configurado', clave=CLAVE_ENSAYO, transporte=transporte)
        self.assertEqual(analizar(DOCUMENTOS, CONTEXTO, proveedor)['ia']['estado'], 'disponible')

    def test_conectar_no_contacta_proveedor_y_no_acepta_identidades_o_claves_invalidas(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': CLAVE_ENSAYO}, clear=True):
            self.assertIsNone(proveedor_desde_entorno('openai'))
            self.assertEqual(proveedor_desde_entorno('openai', modelo='modelo-1').version, 'modelo-1')
        for nombre, modelo, clave in (
            ('url-elegida-por-cliente', 'modelo', CLAVE_ENSAYO),
            ('openai', 'https://example.invalid', CLAVE_ENSAYO),
            ('anthropic', 'modelo', 'clave\r\nInyeccion: prueba'),
        ):
            with self.subTest(nombre=nombre), self.assertRaises(ValueError):
                crear_proveedor(nombre, modelo=modelo, clave=clave)

    def test_no_llama_transporte_si_falta_autorizacion_o_fragmentos(self):
        llamadas = []
        def transporte(*args):
            llamadas.append(args)
            return openai(json.dumps(RESPUESTA))
        proveedor = crear_proveedor('openai', modelo='modelo', clave=CLAVE_ENSAYO, transporte=transporte)
        self.assertEqual(analizar(DOCUMENTOS, {}, proveedor)['ia']['estado'], 'no_disponible')
        self.assertEqual(analizar([{**DOCUMENTOS[0], 'fragmentos': []}], CONTEXTO, proveedor)['ia']['estado'], 'error')
        self.assertEqual(llamadas, [])

    def test_respuestas_incompletas_rechazadas_y_json_duplicado_o_falso(self):
        casos = (
            ('openai', openai('{}', status='incomplete')),
            ('openai', openai('{}', tipo='refusal')),
            ('openai', openai('{"esquema":"sigelc.revision.1","hallazgos":[],"hallazgos":[]}')),
            ('anthropic', anthropic('{}', final='max_tokens')),
            ('anthropic', anthropic('{}', final='refusal')),
            ('anthropic', anthropic('{"esquema":"sigelc.revision.1","hallazgos":[NaN]}')),
        )
        for nombre, respuesta in casos:
            with self.subTest(nombre=nombre, respuesta=respuesta):
                proveedor = crear_proveedor(
                    nombre, modelo='modelo', clave=CLAVE_ENSAYO, transporte=lambda *args: respuesta,
                )
                resultado = analizar(DOCUMENTOS, CONTEXTO, proveedor)
                self.assertEqual(resultado['ia']['estado'], 'error')
                self.assertEqual(resultado['hallazgos'], [])

    def test_transporte_no_acepta_destinos_arbitrarios_o_redirecciones(self):
        with self.assertRaises(ErrorProveedor):
            _enviar('https://example.invalid/capturar', {}, {})
        self.assertIsNone(_SinRedirecciones().redirect_request(
            None, None, 307, '', {}, 'https://example.invalid/capturar',
        ))

    def test_error_http_oculta_cuerpo_y_datos_del_proveedor(self):
        error = HTTPError(OPENAI, 403, 'MENSAJE INTERNO', {}, BytesIO(b'CUERPO PRIVADO'))
        with patch('gestion_contractual.revision_proveedores.build_opener') as crear:
            crear.return_value.open.side_effect = error
            with self.assertRaises(ErrorProveedor) as capturado:
                _enviar(OPENAI, {'Authorization': f'Bearer {CLAVE_ENSAYO}'}, {})
        self.assertNotIn(CLAVE_ENSAYO, str(capturado.exception))
        self.assertNotIn('PRIVADO', str(capturado.exception))
        self.assertTrue(error.fp.closed)

    def test_transporte_limita_respuesta_antes_de_decodificar(self):
        class Respuesta(BytesIO):
            status = 200
            headers = Message()
        respuesta = Respuesta(b'x' * 64)
        respuesta.headers['Content-Type'] = 'application/json'
        with patch('gestion_contractual.revision_proveedores.build_opener') as crear, \
                patch('gestion_contractual.revision_proveedores.MAX_RESPUESTA', 32):
            crear.return_value.open.return_value = respuesta
            with self.assertRaisesRegex(ErrorProveedor, 'supera el límite'):
                _enviar(OPENAI, {}, {})
