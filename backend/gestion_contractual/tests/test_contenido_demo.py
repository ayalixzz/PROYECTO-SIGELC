from io import BytesIO
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from ..contenido_demo import ROTULO, TITULOS, paquetes_ficticios, plantilla_ficticia
from ..docx_requerimientos import procesar_docx
from ..revision_analisis import analizar, comparar
from ..revision_ingesta import recibir_paquete


class ContenidoDemoTest(SimpleTestCase):
    def test_plantillas_validas_reproducibles_y_rotuladas(self):
        for tipo in TITULOS:
            with self.subTest(tipo=tipo):
                nombre, contenido = plantilla_ficticia(tipo)
                self.assertEqual((nombre, contenido), plantilla_ficticia(tipo))
                self.assertIn('ficticia', nombre)
                _, marcadores, _ = procesar_docx(contenido)
                if tipo == 'observaciones':
                    requeridos = {'rotulo_borrador', 'observaciones_documentales'}
                else:
                    requeridos = {'plazo_dias_habiles', 'fecha_vencimiento', 'documentos_solicitados'}
                self.assertTrue(requeridos <= marcadores)
                self.assertIn('numero_contrato', marcadores)
                datos = dict.fromkeys(marcadores, 'Ejemplo ficticio')
                datos['numero_contrato'] = 'SIGELC-DEMO-001'
                resultado, _, pendientes = procesar_docx(contenido, datos)
                with ZipFile(BytesIO(resultado)) as paquete:
                    xml = paquete.read('word/document.xml').decode()
                self.assertFalse(pendientes)
                self.assertIn(ROTULO, xml)
                self.assertIn('SIGELC-DEMO-001', xml)

    def test_dos_entregas_documentan_inconsistencia_y_correccion(self):
        contexto = {
            'numero_contrato': 'SIGELC-DEMO-001',
            'contratista_nombre': 'Consorcio ficticio Horizonte',
            'valor_actual': '12500000.00',
        }
        paquetes = list(paquetes_ficticios())
        self.assertEqual(paquetes, list(paquetes_ficticios()))
        entregas = [
            [{'id': f'{numero}.{i}', 'nombre': d.nombre, 'sha256': d.sha256,
              'clasificacion': d.clasificacion, 'fragmentos': d.fragmentos,
              'advertencias': d.advertencias}
             for i, d in enumerate(recibir_paquete([SimpleUploadedFile(nombre, contenido)])) if d.tipo != 'zip']
            for numero, (nombre, contenido) in enumerate(paquetes, 1)
        ]
        previo, actual = (analizar(documentos, contexto) for documentos in entregas)
        self.assertEqual([h['regla'] for h in previo['hallazgos']], ['identidad'])
        self.assertFalse(actual['hallazgos'])
        comparacion = comparar(*entregas, previo['hallazgos'], actual['hallazgos'])
        self.assertEqual(sorted(d['estado'] for d in comparacion['documentos']), ['modificado', 'sin_cambio'])
        self.assertEqual([h['estado'] for h in comparacion['hallazgos']], ['corregido'])
