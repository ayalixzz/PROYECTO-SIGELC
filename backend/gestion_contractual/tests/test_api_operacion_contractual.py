import csv
from io import StringIO
from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from gestion_contractual.models import Auditoria, Contrato
from gestion_contractual.services import iniciar_seguimiento

from . import test_api_demo


@skipUnless(getattr(settings, 'SIGELC_DEMO', False), 'Requiere el perfil de demo aislada.')
class OperacionContractualAPITest(TestCase):
    setUpTestData = classmethod(test_api_demo.ApiDemoTest.setUpTestData.__func__)
    abrir_sesion = test_api_demo.ApiDemoTest.abrir_sesion
    escribir = test_api_demo.ApiDemoTest.escribir
    contrato_nuevo = test_api_demo.ApiDemoTest.contrato_nuevo

    def setUp(self):
        test_api_demo.ApiDemoTest.setUp(self)
        self.abrir_sesion()

    def cargar(self, nombres, operacion=None):
        return self.client.post(
            self.url + 'documentos/cargar/', {
                'operacion': operacion or uuid4(),
                'archivos': [SimpleUploadedFile(nombre, b'%PDF-1.4\nSoporte ficticio') for nombre in nombres],
            }, format='multipart', HTTP_X_CSRFTOKEN=self.csrf,
        )

    def test_carga_multiple_se_muestra_y_conserva_un_registro_por_archivo(self):
        operacion = uuid4()
        primera = self.cargar(['primero.pdf', 'segundo.pdf'], operacion)
        self.assertEqual(primera.status_code, 201, primera.content)
        self.assertEqual(len(primera.json()), 2)
        repetida = self.cargar(['primero.pdf', 'segundo.pdf'], operacion)
        self.assertEqual([d['id'] for d in repetida.json()], [d['id'] for d in primera.json()])
        detalle = self.client.get(self.url).json()
        self.assertTrue(detalle['permisos']['cargar_documentos'])
        self.assertTrue({d['id'] for d in primera.json()} <= {d['id'] for d in detalle['documentos']})
        self.assertTrue(all(d['descarga_url'] for d in primera.json()))

    def test_carga_rechazada_no_deja_soportes_parciales(self):
        antes = self.contrato.documentos.count()
        respuesta = self.cargar(['primero.pdf', 'no-admitido.txt'])
        self.assertEqual(respuesta.status_code, 400, respuesta.content)
        self.assertEqual(self.contrato.documentos.count(), antes)

    def test_exporta_filtros_y_neutraliza_valores_interpretables_como_formulas(self):
        datos = self.contrato_nuevo()
        datos.update(numero_contrato='EXP-FICTICIO', contratista_nombre='=SUM(1+1)')
        creado = self.escribir('post', '/api/contratos/', datos)
        self.assertEqual(creado.status_code, 201, creado.content)
        exportacion = self.client.get('/api/contratos/exportar/', {'buscar': 'EXP-FICTICIO'})
        self.assertEqual(exportacion.status_code, 200)
        filas = list(csv.DictReader(StringIO(exportacion.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['Contratista'], "'=SUM(1+1)")
        self.assertEqual(filas[0]['Valor total'], '85000.75')

    def test_cambio_manual_exige_confirmacion_y_queda_en_el_historial(self):
        datos = self.contrato_nuevo()
        respuesta = self.escribir('post', '/api/contratos/', datos)
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        contrato = Contrato.objects.get(pk=respuesta.json()['id'])
        iniciar_seguimiento(contrato_id=contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True)
        ruta = f'/api/contratos/{contrato.pk}/transiciones/'
        opciones = self.client.get(ruta)
        self.assertEqual(opciones.status_code, 200, opciones.content)
        self.assertTrue(opciones.json()['estados'])
        destino = opciones.json()['estados'][0]
        payload = {
            'estado_nuevo_id': destino['id'], 'instancia_esperada': opciones.json()['instancia'],
            'fecha_efectiva': datos['fecha_asignacion'], 'motivo': 'Seguimiento ficticio.',
        }
        rechazo = self.escribir('post', ruta, {**payload, 'confirmado': False})
        self.assertEqual(rechazo.status_code, 400, rechazo.content)
        cambio = self.escribir('post', ruta, {**payload, 'confirmado': True})
        self.assertEqual(cambio.status_code, 200, cambio.content)
        self.assertEqual(cambio.json()['estado_actual']['id'], destino['id'])
        self.assertTrue(Auditoria.objects.filter(accion='cambio_estado', identificador_registro=str(contrato.pk)).exists())
