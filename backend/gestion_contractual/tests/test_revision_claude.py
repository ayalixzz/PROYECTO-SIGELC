from datetime import timedelta
from importlib.util import find_spec
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from . import test_revision_flujo
from .calendarios_ficticios import aprobar_ficticio
from ..management.commands.sembrar_demo import incorporar_plantilla
from ..mcp_demo import NUMERO
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
