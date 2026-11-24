"""Independent protocol smoke test. Read-only by default."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probar(servidor, sin_autorizacion):
    parametros = StdioServerParameters(command=sys.executable, args=[str(servidor)])
    async with stdio_client(parametros) as (lectura, escritura):
        async with ClientSession(lectura, escritura) as cliente:
            await cliente.initialize()
            herramientas = [t.name for t in (await cliente.list_tools()).tools]
            assert len(herramientas) == 6

            async def llamar(nombre, **datos):
                respuesta = await cliente.call_tool(f'sigelc_{nombre}', datos)
                assert not respuesta.isError, respuesta.content
                return respuesta.structuredContent or json.loads(respuesta.content[0].text)

            if sin_autorizacion:
                respuesta = await cliente.call_tool('sigelc_buscar_contratos', {'texto_busqueda': 'SIGELC-DEMO-001'})
                assert respuesta.isError, 'Hay autorización activa. Omita --sin-autorizacion.'
                assert 'Autorice localmente' in respuesta.content[0].text, respuesta.content
                return {'herramientas': herramientas, 'sin_autorizacion': 'acceso_rechazado'}

            resultados = await llamar('buscar_contratos', texto_busqueda='SIGELC-DEMO-001')
            contrato = resultados['resultados'][0]['id']
            ficha = await llamar('consultar_expediente', contrato_id=contrato)
            assert ficha['numero'] == 'SIGELC-DEMO-001'
            entregas = ficha['entregas'][:2]
            assert len(entregas) == 2, 'Autorice dos entregas ficticias.'
            totales = []
            for entrada in entregas:
                datos = await llamar('consultar_entrega', contrato_id=contrato, entrega_id=entrada['id'])
                fuente = next(d for d in datos['resultados'] if d['tipo'] != 'zip')
                fragmentos = await llamar('consultar_entrega', contrato_id=contrato,
                                           entrega_id=entrada['id'], documento_id=fuente['id'])
                assert fragmentos['resultados'] and fragmentos['contenido_no_confiable']
                totales.append(len(fragmentos['resultados']))
            comparar = await llamar('comparar_entregas', contrato_id=contrato,
                                    entrega_anterior_id=entregas[0]['id'], entrega_actual_id=entregas[1]['id'])
            observaciones = await llamar('consultar_observaciones', contrato_id=contrato)
            denegado = await cliente.call_tool('sigelc_consultar_expediente', {'contrato_id': contrato + 1})
            assert denegado.isError
            return {'herramientas': herramientas, 'contrato': ficha['numero'],
                    'entregas': [r['version'] for r in entregas], 'fragmentos': totales,
                    'comparacion': [h['estado'] for h in comparar['resultados']],
                    'observaciones': len(observaciones['resultados']), 'otro_expediente': 'rechazado'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sin-autorizacion', action='store_true')
    args = parser.parse_args()
    raiz = Path(__file__).resolve().parents[1]
    print(json.dumps(asyncio.run(probar(raiz / 'backend' / 'sigelc_mcp.py', args.sin_autorizacion)), ensure_ascii=False))
