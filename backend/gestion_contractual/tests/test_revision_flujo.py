from io import BytesIO
from uuid import uuid4
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from . import test_api_demo
from .test_revision_ingesta import docx, comprimido
from .calendarios_ficticios import aprobar_ficticio
from gestion_contractual.management.commands.sembrar_demo import incorporar_plantilla
from gestion_contractual.models import Auditoria, ObservacionDocumental, TipoRequerimiento
from gestion_contractual.revision_analisis import ESQUEMA, ProveedorAutorizado


class RevisionFlujoTest(TestCase):
    setUpTestData = classmethod(test_api_demo.ApiDemoTest.setUpTestData.__func__)
    setUp = test_api_demo.ApiDemoTest.setUp
    abrir_sesion = test_api_demo.ApiDemoTest.abrir_sesion
    escribir = test_api_demo.ApiDemoTest.escribir
    comprobar_descarga = test_api_demo.ApiDemoTest.comprobar_descarga

    def paquete(self, *, corregido=False, anterior=None, operacion=None):
        numero = self.contrato.numero_contrato if corregido else 'FICTICIO-ERRONEO-121'
        contenido = comprimido({'evidencia/soporte.docx': docx(f'Contrato: {numero}')})
        datos = {'archivos': SimpleUploadedFile('entrega.zip', contenido), 'operacion': operacion or uuid4()}
        if anterior is not None:
            datos['anterior_id'] = anterior
        return self.client.post(self.url + 'revisiones/', datos, format='multipart', HTTP_X_CSRFTOKEN=self.csrf)

    def accion(self, revision, ruta, esperado=200, **datos):
        datos.setdefault('operacion', str(uuid4()))
        respuesta = self.escribir('post', f'{self.url}revisiones/{revision["id"]}/{ruta}/', datos)
        self.assertEqual(respuesta.status_code, esperado, respuesta.content)
        return respuesta.json()

    def test_entrega_decisiones_dos_docx_y_comparacion(self):
        self.abrir_sesion()
        self.contrato.fecha_asignacion = self.contrato.fecha_suscripcion
        self.contrato.save()
        aprobar_ficticio(self.abogado, self.contrato.fecha_asignacion.year)
        self.assertEqual(self.escribir('post', self.url + 'seguimiento/', {'confirmado': True}).status_code, 201)
        incorporar_plantilla(TipoRequerimiento.PRIMERO, self.abogado)
        incorporar_plantilla(TipoRequerimiento.OBSERVACIONES, self.abogado)
        creada = self.paquete()
        self.assertEqual(creada.status_code, 201, creada.content)
        revision = creada.json()
        self.assertTrue(revision['puede_generar'])
        self.assertEqual(revision['plazo_dias_habiles'], 15)
        self.assertEqual({d['tipo'] for d in revision['documentos']}, {'zip', 'docx'})
        revision = self.accion(revision, 'analizar')
        self.assertEqual(revision['analisis']['ia']['estado'], 'no_disponible')
        hallazgo = next(h for h in revision['analisis']['hallazgos'] if h['evidencia'])
        self.assertTrue(any('FICTICIO-ERRONEO-121' in e['fragmento'] for e in hallazgo['evidencia']))
        self.accion(revision, 'generar', 400, tipo='acta', observaciones=[999999], confirmado=True)
        revision = self.accion(revision, 'decisiones', accion='editar', hallazgo_id=hallazgo['id'],
            descripcion='Aclarar número del contrato del soporte de prueba.', fundamento='Cotejo humano ficticio.')
        decision = revision['decisiones'][-1]['id']
        self.accion(revision, 'decisiones', 400, accion='rechazar', hallazgo_id=hallazgo['id'], fundamento='Decisión obsoleta.')
        manual = self.accion(revision, 'decisiones', accion='manual', descripcion='Texto excluido de prueba.',
            fundamento='Observación manual de prueba que no se seleccionará.')
        self.assertEqual(len(manual['decisiones']), 2)
        for tipo in ('acta', 'requerimiento'):
            operacion = str(uuid4())
            revision = self.accion(revision, 'generar', tipo=tipo, observaciones=[decision], confirmado=True, operacion=operacion)
            reintento = self.accion(revision, 'generar', tipo=tipo, observaciones=[decision], confirmado=True, operacion=operacion)
            self.assertEqual(revision['borradores'], reintento['borradores'])
            archivo = self.comprobar_descarga(revision['borradores'][-1]['documento']['descarga_url'])
            with ZipFile(BytesIO(archivo)) as word:
                texto = word.read('word/document.xml').decode()
            self.assertIn('Aclarar número del contrato', texto)
            self.assertNotIn('Texto excluido', texto)
        self.assertEqual(ObservacionDocumental.objects.filter(contrato=self.contrato).count(), 1)
        self.assertTrue(Auditoria.objects.filter(accion='revision_generacion').exists())
        siguiente = self.paquete(corregido=True, anterior=revision['id'])
        self.assertEqual(siguiente.status_code, 201, siguiente.content)
        nueva = self.accion(siguiente.json(), 'analizar')
        self.assertEqual(nueva['version'], 2)
        self.assertTrue(any(d['estado'] == 'modificado' for d in nueva['comparacion']['documentos']))
        self.assertTrue(any(h['estado'] == 'corregido' and h['evidencia_actual'] for h in nueva['comparacion']['hallazgos']))
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual.codigo, TipoRequerimiento.PRIMERO)

    def test_recepcion_reintento_csrf_descarga_y_version_obsoleta(self):
        self.abrir_sesion()
        url = self.url + 'revisiones/'
        self.assertEqual(self.client.post(url, {}, format='multipart').status_code, 403)
        operacion = str(uuid4())
        creada = self.paquete(operacion=operacion)
        self.assertEqual(creada.status_code, 201, creada.content)
        revision = creada.json()
        self.assertEqual(self.client.get(url).json()[0]['id'], revision['id'])
        self.assertEqual(self.paquete(operacion=operacion).json()['id'], revision['id'])
        self.assertEqual(self.paquete(corregido=True, operacion=operacion).status_code, 400)
        self.assertEqual(self.paquete().status_code, 400)
        fuente = revision['documentos'][0]
        self.assertTrue(self.comprobar_descarga(fuente['descarga_url']).startswith(b'PK'))
        self.client.logout()
        self.assertEqual(self.client.get(fuente['descarga_url']).status_code, 403)

    def test_manual_rechaza_evidencia_falsa_y_rechazo_no_se_genera(self):
        self.abrir_sesion()
        revision = self.accion(self.paquete().json(), 'analizar')
        fuente = next(d for d in revision['documentos'] if d['tipo'] == 'docx')
        self.accion(revision, 'decisiones', 400, accion='manual', descripcion='Propuesta ficticia.',
            documento_id=fuente['id'], ubicacion=fuente['fragmentos'][0]['ubicacion'], fragmento='Cita inventada.')
        hallazgo = revision['analisis']['hallazgos'][0]
        revision = self.accion(revision, 'decisiones', accion='rechazar', hallazgo_id=hallazgo['id'], fundamento='Cotejo insuficiente.')
        self.assertEqual(revision['decisiones'][-1]['accion'], 'rechazar')

    def test_analisis_local_incluye_diagnostico_y_proveedor_desactivado(self):
        self.abrir_sesion()
        revision = self.accion(self.paquete().json(), 'analizar')
        juridico = revision['analisis']['juridico']
        self.assertIsNone(juridico['diagnostico_tecnico_financiero']['pagos_acreditados'])
        self.assertEqual(juridico['diagnostico_tecnico_financiero']['operaciones_acreditadas'], 0)
        self.assertIsNone(juridico['terminos_verificados'])
        self.assertIsNone(revision['proveedor_externo'])
        fuente = next(d for d in revision['documentos'] if d['tipo'] == 'docx')
        self.accion(revision, 'analizar-ia', 400, documentos=[fuente['id']], confirmado=True)
        self.assertFalse(Auditoria.objects.filter(accion='revision_ia_solicitud').exists())

    def test_ia_externa_consiente_fuentes_exactas_y_no_reenvia_al_reintentar(self):
        self.abrir_sesion()
        revision = self.accion(self.paquete().json(), 'analizar')
        doc = next(d for d in revision['documentos'] if d['tipo'] == 'docx')
        zip_id = next(d['id'] for d in revision['documentos'] if d['tipo'] == 'zip')
        originales = len(revision['analisis']['hallazgos'])
        recibidos = []

        def ejecutar(payload):
            recibidos.append(payload)
            fuente = payload['fragmentos'][0]
            return {'esquema': ESQUEMA, 'hallazgos': [{
                'id': 'verificar-cita', 'categoria': 'documental',
                'descripcion': 'Revisar el campo citado.',
                'documento_id': fuente['documento_id'], 'ubicacion': fuente['ubicacion'],
                'fragmento': fuente['texto'], 'regla': 'identidad', 'gravedad': 'baja',
                'justificacion': 'Comprobación de la evidencia.',
                'incertidumbre': 'alta', 'recomendacion': 'Cotejar el original.',
            }]}

        proveedor = ProveedorAutorizado(ejecutor=ejecutar, nombre='OpenAI', version='modelo-de-prueba')
        with patch('gestion_contractual.revision_ia_servicio.proveedor_configurado', return_value=('openai', proveedor)):
            self.accion(revision, 'analizar-ia', 400, documentos=[doc['id']], confirmado=False)
            self.accion(revision, 'analizar-ia', 400, documentos=[zip_id], confirmado=True)
            self.assertEqual(recibidos, [])
            operacion = str(uuid4())
            resultado = self.accion(revision, 'analizar-ia', documentos=[doc['id']], confirmado=True, operacion=operacion)
            self.accion(revision, 'analizar-ia', documentos=[doc['id']], confirmado=True, operacion=operacion)
            self.accion(revision, 'analizar-ia', 400, documentos=[doc['id']], confirmado=True)
        self.assertEqual(len(recibidos), 1)
        self.assertEqual({f['documento_id'] for f in recibidos[0]['fragmentos']}, {doc['id']})
        self.assertNotIn('contratista_identificacion', recibidos[0]['contexto_minimo'])
        self.assertEqual(resultado['analisis']['ia']['estado'], 'disponible')
        self.assertEqual(len(resultado['analisis']['hallazgos']), originales + 1)
        self.assertFalse(resultado['decisiones'])
        self.assertEqual(Auditoria.objects.filter(accion='revision_ia_solicitud').count(), 1)
        self.assertEqual(Auditoria.objects.filter(accion='revision_ia_resultado').count(), 1)
