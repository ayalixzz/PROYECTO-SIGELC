from datetime import timedelta
from importlib.util import find_spec
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from uuid import UUID, uuid4
from zipfile import ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from . import test_revision_flujo
from .calendarios_ficticios import aprobar_ficticio
from ..management.commands.sembrar_demo import incorporar_plantilla
from ..mcp_demo import NIT_DEMO, NUMERO, OPERACIONES_HOSPEDADAS, PERFIL_HOSPEDADO
from ..models import Auditoria, DocumentoGenerado, TipoRequerimiento
from ..revision_claude import VIGENCIA, recibir_propuestas


@skipUnless(getattr(settings, 'SIGELC_DEMO', False) and find_spec('mcp'), 'Requiere demo y SDK MCP.')
class RevisionClaudeTest(TestCase):
    base = test_revision_flujo.RevisionFlujoTest
    setUpTestData = classmethod(base.setUpTestData.__func__)
    setUp = base.setUp
    abrir_sesion = base.abrir_sesion
    escribir = base.escribir
    paquete = base.paquete
    accion = base.accion
    comprobar_descarga = base.comprobar_descarga

    def preparar(self, *, seguimiento=False):
        from sigelc_mcp import Propuesta

        self.abrir_sesion()
        self.contrato.numero_contrato = NUMERO
        self.contrato.contratista_identificacion = 'FICTICIO-001'
        self.contrato.fecha_asignacion = self.contrato.fecha_suscripcion
        self.contrato.save()
        Auditoria.objects.create(usuario=self.abogado, accion='registrar_expediente_demo',
            entidad_afectada='Contrato', identificador_registro=str(self.contrato.pk))
        if seguimiento:
            aprobar_ficticio(self.abogado, self.contrato.fecha_asignacion.year)
            self.assertEqual(self.escribir('post', self.url + 'seguimiento/', {'confirmado': True}).status_code, 201)
        datos = self.accion(self.paquete().json(), 'analizar')
        revision = self.contrato.revisiones_inteligentes.get(pk=datos['id'])
        fuente = revision.fuentes_revision.get(tipo='docx')
        cita = fuente.fragmentos[0]
        propuesta = Propuesta(categoria='documental', descripcion='Claude propone cotejar el número.',
            evidencia=[{'documento_id': fuente.pk, 'ubicacion': cita['ubicacion'], 'fragmento': cita['texto']}],
            fundamento='Comparar con el número del expediente.', recomendacion='Verificar el soporte.',
            incertidumbre='media')
        return revision, datos, propuesta

    def test_flujo_autorizacion_propuesta_evidencia_y_word_con_revision_humana(self):
        revision, datos, propuesta = self.preparar(seguimiento=True)
        incorporar_plantilla(TipoRequerimiento.PRIMERO, self.abogado)
        incorporar_plantilla(TipoRequerimiento.OBSERVACIONES, self.abogado)
        with override_settings(SIGELC_CLAUDE_ENTREGAS=(revision.pk,)):
            primera = self.accion(datos, 'solicitar-claude')['claude']['solicitud']
            segunda = self.accion(datos, 'solicitar-claude')['claude']['solicitud']
            self.assertEqual(primera, segunda)
            self.assertEqual(primera['estado'], 'pendiente')
            self.assertEqual(revision.eventos_revision.filter(tipo='mcp_solicitud').count(), 1)

            from uuid import UUID
            parametros = {'usuario': self.abogado, 'revision': revision, 'solicitud_id': UUID(primera['id'])}
            falsa = propuesta.evidencia[0].model_copy(update={'fragmento': 'Prueba de cita ficticia que no existe'})
            with self.assertRaisesMessage(ValidationError, 'La cita no existe'):
                recibir_propuestas(**parametros, propuestas=[propuesta.model_copy(update={'evidencia': [falsa]})])
            self.assertFalse(revision.eventos_revision.filter(tipo='mcp_propuestas').exists())
            recibida = recibir_propuestas(**parametros, propuestas=[propuesta])
            self.assertEqual(recibida, recibir_propuestas(**parametros, propuestas=[propuesta]))
            with self.assertRaisesMessage(ValidationError, 'corresponde a otra operación'):
                recibir_propuestas(**parametros, propuestas=[])
            self.assertEqual(revision.eventos_revision.filter(tipo='mcp_propuestas').count(), 1)
            self.assertFalse(DocumentoGenerado.objects.filter(contrato=self.contrato).exists())

            detalle = self.client.get(f'{self.url}revisiones/{revision.pk}/').json()
            self.assertEqual(detalle['claude']['solicitud']['estado'], 'recibida')
            hallazgo = next(h for h in detalle['analisis']['hallazgos'] if h.get('origen') == 'claude_desktop')
            self.assertEqual(hallazgo['estado'], 'PENDIENTE_REVISION')
            self.assertEqual(detalle['decisiones'], [])
            self.accion(detalle, 'generar', 400, tipo='acta', observaciones=[999999], confirmado=True)
            aprobada = self.accion(detalle, 'decisiones', accion='editar', hallazgo_id=hallazgo['id'],
                descripcion='Observación de Claude revisada por el abogado.', fundamento='Cotejo del documento.')
            generada = self.accion(detalle, 'generar', tipo='acta', confirmado=True,
                                  observaciones=[aprobada['decisiones'][0]['id']])
            documento = self.comprobar_descarga(generada['borradores'][-1]['documento']['descarga_url'])
            with ZipFile(BytesIO(documento)) as paquete:
                self.assertIn('Claude revisada por el abogado', paquete.read('word/document.xml').decode())
        self.contrato.refresh_from_db()
        self.assertEqual(self.contrato.estado_actual.codigo, TipoRequerimiento.PRIMERO)

    def test_ambito_csrf_y_solicitud_inexistente_o_caducada(self):
        from uuid import UUID

        revision, datos, propuesta = self.preparar()
        ruta = f'{self.url}revisiones/{revision.pk}/solicitar-claude/'
        self.accion(datos, 'solicitar-claude', 400)
        with override_settings(SIGELC_CLAUDE_ENTREGAS=(revision.pk,)):
            self.assertEqual(self.client.post(ruta, {'operacion': str(uuid4())}, format='json').status_code, 403)
            parametros = {'usuario': self.abogado, 'revision': revision, 'propuestas': [propuesta]}
            with self.assertRaisesMessage(ValidationError, 'no pertenece'):
                recibir_propuestas(**parametros, solicitud_id=uuid4())
            id_solicitud = UUID(self.accion(datos, 'solicitar-claude')['claude']['solicitud']['id'])
            with patch('gestion_contractual.revision_claude.timezone.now', return_value=timezone.now() + VIGENCIA):
                estado = self.client.get(f'{self.url}revisiones/{revision.pk}/').json()['claude']
                self.assertEqual(estado['solicitud']['estado'], 'vencida')
                self.assertTrue(estado['puede_solicitar'])
                with self.assertRaisesMessage(ValidationError, 'venció'):
                    recibir_propuestas(**parametros, solicitud_id=id_solicitud)

    def test_respuesta_sin_observaciones_y_entrega_no_vigente(self):
        from uuid import UUID

        revision, datos, propuesta = self.preparar()
        with override_settings(SIGELC_CLAUDE_ENTREGAS=(revision.pk,)):
            solicitud = UUID(self.accion(datos, 'solicitar-claude')['claude']['solicitud']['id'])
            respuesta = recibir_propuestas(usuario=self.abogado, revision=revision, solicitud_id=solicitud, propuestas=[])
            self.assertEqual(respuesta['propuestas'], 0)
            self.assertEqual(self.paquete(anterior=revision.pk).status_code, 201)
            self.accion(datos, 'solicitar-claude', 400)

    def test_demo_hospedada_analiza_varios_documentos_recien_cargados_sin_compartir_el_resto(self):
        from .test_revision_ingesta import comprimido, docx
        from .. import mcp_tools
        from ..mcp_tools import Propuesta

        self.abrir_sesion()
        self.contrato.numero_contrato = NUMERO
        self.contrato.contratista_identificacion = 'FICTICIO-001'
        self.contrato.save()
        self.contrato.entidad.nit = NIT_DEMO
        self.contrato.entidad.save()
        Auditoria.objects.create(usuario=self.abogado, accion='registrar_expediente_demo',
            entidad_afectada='Contrato', identificador_registro=str(self.contrato.pk),
            datos_nuevos={'identificador_demo': NUMERO})
        inicial = self.paquete(operacion=dict(OPERACIONES_HOSPEDADAS)[1]).json()
        anterior = self.paquete(anterior=inicial['id'], operacion=dict(OPERACIONES_HOSPEDADAS)[2]).json()
        entrega = self.client.post(self.url + 'revisiones/', {
            'archivos': SimpleUploadedFile('entrega.zip', comprimido({
                f'Soporte {i}.docx': docx(f'Texto ficticio {i}.') for i in range(1, 4)
            })), 'operacion': uuid4(), 'anterior_id': anterior['id'],
        }, format='multipart', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(entrega.status_code, 201, entrega.content)
        revision = self.contrato.revisiones_inteligentes.get(pk=entrega.json()['id'])
        fuentes = list(revision.fuentes_revision.exclude(tipo='zip').order_by('pk'))
        primera, segunda, excluida = fuentes
        seleccion = [primera.pk, segunda.pk]
        zip_id = revision.fuentes_revision.get(tipo='zip').pk

        with (
            override_settings(SETTINGS_MODULE=PERFIL_HOSPEDADO, ALLOWED_HOSTS=['testserver'],
                SIGELC_MCP_ENTREGAS=(inicial['id'], anterior['id']), SIGELC_MCP_PROPUESTAS=True),
            patch('gestion_contractual.revision_claude.verificar_base'),
        ):
            sin_analizar = self.client.get(f'{self.url}revisiones/{revision.pk}/').json()
            self.assertFalse(sin_analizar['claude']['puede_solicitar'])
            datos = self.accion(entrega.json(), 'analizar')
            self.assertFalse(datos['claude']['puede_consultar'])
            self.assertTrue(datos['claude']['puede_solicitar'])
            self.assertEqual(datos['claude']['documentos_disponibles'], [f.pk for f in fuentes])
            self.accion(datos, 'solicitar-claude', 400, documentos=seleccion, confirmado=True)
            self.accion(datos, 'solicitar-claude', 400, documentos=[zip_id], confirmado=True, ficticios=True)
            self.accion(datos, 'solicitar-claude', 400, documentos=[primera.pk] * 2, confirmado=True, ficticios=True)
            creada = self.accion(datos, 'solicitar-claude', documentos=seleccion, confirmado=True, ficticios=True)
            solicitud = UUID(creada['claude']['solicitud']['id'])
            self.assertEqual(creada['claude']['solicitud']['documentos'], seleccion)
            self.accion(datos, 'solicitar-claude', 400,
                       documentos=[excluida.pk], confirmado=True, ficticios=True)
            permiso = {'entregas': [inicial['id'], anterior['id']], 'propuestas': True}
            contexto = mcp_tools._acceso.set(lambda: (self.abogado, self.contrato, permiso))
            try:
                consultar = mcp_tools.sigelc_consultar_entrega.__wrapped__
                with self.assertRaisesMessage(ValidationError, 'Entrega no autorizada'):
                    consultar(self.contrato.pk, revision.pk)
                with self.assertRaisesMessage(ValidationError, 'no pertenece'):
                    consultar(self.contrato.pk, revision.pk, solicitud_id=uuid4())
                args = (self.contrato.pk, revision.pk)
                permiso['propuestas'] = False
                with self.assertRaisesMessage(ValidationError, 'requiere una autorización vigente'):
                    consultar(*args, solicitud_id=solicitud)
                permiso['propuestas'] = True
                with (patch('gestion_contractual.revision_claude.timezone.now', return_value=timezone.now() + VIGENCIA),
                      self.assertRaisesMessage(ValidationError, 'venció')):
                    consultar(*args, solicitud_id=solicitud)
                pagina1 = consultar(*args, solicitud_id=solicitud, limite=1)
                pagina2 = consultar(*args, solicitud_id=solicitud, limite=1, pagina=2)
                self.assertTrue(pagina1['hay_mas'])
                self.assertFalse(pagina2['hay_mas'])
                self.assertEqual([p['resultados'][0]['id'] for p in [pagina1, pagina2]], seleccion)
                with self.assertRaisesMessage(ValidationError, 'Documento no autorizado'):
                    consultar(*args, solicitud_id=solicitud, documento_id=excluida.pk)

                def cita(fuente):
                    fragmento = fuente.fragmentos[0]
                    return {'documento_id': fuente.pk, 'ubicacion': fragmento['ubicacion'],
                            'fragmento': fragmento['texto']}

                propuesta = Propuesta(categoria='documental', descripcion='Cotejo de prueba.',
                    evidencia=[cita(primera), cita(segunda)], fundamento='Cotejo de los dos soportes.',
                    recomendacion='Revisión humana.', incertidumbre='baja')
                devolver = mcp_tools.sigelc_entregar_propuestas.__wrapped__
                ajena = propuesta.model_copy(update={'evidencia': [mcp_tools.Cita(**cita(excluida))]})
                with self.assertRaisesMessage(ValidationError, 'La cita no existe'):
                    devolver(*args, solicitud, [ajena])
                resultado = devolver(*args, solicitud, [propuesta])
                self.assertEqual(resultado['propuestas'], 1)
                with self.assertRaisesMessage(ValidationError, 'selección de documentos pendiente'):
                    consultar(*args, solicitud_id=solicitud)
            finally:
                mcp_tools._acceso.reset(contexto)
            detalle = self.client.get(f'{self.url}revisiones/{revision.pk}/').json()
            hallazgo = next(h for h in detalle['analisis']['hallazgos'] if h.get('origen') == 'claude_desktop')
            self.assertEqual(hallazgo['estado'], 'PENDIENTE_REVISION')
            self.assertEqual([cita['documento_id'] for cita in hallazgo['evidencia']], seleccion)
            self.assertEqual(detalle['claude']['solicitud']['estado'], 'recibida')
