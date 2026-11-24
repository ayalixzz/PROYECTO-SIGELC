from importlib.util import find_spec
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import TestCase

from . import test_revision_flujo
from .calendarios_ficticios import aprobar_ficticio
from .. import mcp_demo
from ..models import Auditoria, EventoRevisionInteligente, TipoRequerimiento
from ..management.commands.sembrar_demo import incorporar_plantilla


@skipUnless(getattr(settings, 'SIGELC_DEMO', False) and find_spec('mcp'), 'Requiere la demo y el SDK opcional.')
class MCPDemoTest(TestCase):
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
        if seguimiento:
            self.contrato.fecha_asignacion = self.contrato.fecha_suscripcion
            self.contrato.save()
            aprobar_ficticio(self.abogado, self.contrato.fecha_asignacion.year)
            self.assertEqual(self.escribir('post', self.url + 'seguimiento/', {'confirmado': True}).status_code, 201)
        datos = self.accion(self.paquete().json(), 'analizar')
        revision = self.contrato.revisiones_inteligentes.get(pk=datos['id'])
        fuente = revision.fuentes_revision.get(tipo='docx')
        fragmento = fuente.fragmentos[0]
        propuesta = Propuesta(categoria='inconsistencia', descripcion='Propuesta ficticia de Claude.',
            evidencia=[{'documento_id': fuente.pk, 'ubicacion': fragmento['ubicacion'], 'fragmento': fragmento['texto']}],
            fundamento='Cotejo con el número registrado.', incertidumbre='media', recomendacion='Revisar el documento.')
        operacion = uuid4()
        permiso = {'version_contrato': self.contrato.version_registro, 'escritura': True,
                   'aprobacion': [str(operacion), mcp_demo.firma_propuesta(revision, operacion, [propuesta])]}
        return revision, permiso, operacion, propuesta

    def test_cita_ubicacion_norma_y_aprobacion_exacta(self):
        revision, permiso, operacion, propuesta = self.preparar()
        for cambio in ({'fragmento': 'Inventado'}, {'ubicacion': 'Página inexistente'}, {'documento_id': 999999}):
            cita = propuesta.evidencia[0].model_copy(update=cambio)
            with self.assertRaisesMessage(ValidationError, 'La cita no existe'):
                mcp_demo.guardar(self.abogado, revision, permiso, operacion, [propuesta.model_copy(update={'evidencia': [cita]})])
        falsa = propuesta.model_copy(update={'referencia_juridica': 'norma_inventada'})
        with self.assertRaisesMessage(ValidationError, 'catálogo verificado'):
            mcp_demo.guardar(self.abogado, revision, permiso, operacion, [falsa])
        alterada = propuesta.model_copy(update={'descripcion': 'Contenido no autorizado.'})
        with self.assertRaisesMessage(ValidationError, 'Autorice localmente'):
            mcp_demo.guardar(self.abogado, revision, permiso, operacion, [alterada])
        self.assertFalse(revision.eventos_revision.filter(tipo='mcp_propuestas').exists())

    def test_permiso_solo_lectura_propiedad_y_entrega_obsoleta(self):
        revision, permiso, operacion, propuesta = self.preparar()
        with self.assertRaisesMessage(ValidationError, 'solo permite lectura'):
            mcp_demo.guardar(self.abogado, revision, {**permiso, 'escritura': False}, operacion, [propuesta])
        with patch('gestion_contractual.revision_flujo.exigir'), self.assertRaises(Http404):
            mcp_demo.guardar(self.otro, revision, permiso, operacion, [propuesta])
        with self.assertRaisesMessage(ValidationError, 'Entrega no autorizada'):
            mcp_demo.entrega(self.contrato, {'entregas': []}, revision.pk)
        self.assertEqual(self.paquete(anterior=revision.pk).status_code, 201)
        with self.assertRaisesMessage(ValidationError, 'última entrega'):
            mcp_demo.guardar(self.abogado, revision, permiso, operacion, [propuesta])

    def test_autorizacion_vinculada_a_usuario_y_revocable(self):
        self.contrato.numero_contrato = mcp_demo.NUMERO
        self.contrato.contratista_identificacion = 'FICTICIO-001'
        self.contrato.save()
        Auditoria.objects.create(usuario=self.abogado, accion='registrar_expediente_demo',
            entidad_afectada='Contrato', identificador_registro=str(self.contrato.pk))
        permiso = {'usuario': self.abogado.pk, 'autenticacion': self.abogado.get_session_auth_hash(),
                   'contrato': self.contrato.pk, 'version_contrato': self.contrato.version_registro,
                   'entregas': [], 'escritura': False}
        credencial = signing.dumps(permiso, salt=mcp_demo.SALT)
        with patch.object(mcp_demo, 'leer_autorizacion', side_effect=['', credencial, credencial]):
            with self.assertRaisesMessage(ValidationError, 'Autorice localmente'):
                mcp_demo.acceso()
            self.assertEqual(mcp_demo.acceso()[1].pk, self.contrato.pk)
            self.abogado.set_unusable_password()
            self.abogado.save(update_fields=['password'])
            with self.assertRaisesMessage(ValidationError, 'La cuenta cambió'):
                mcp_demo.acceso()

    def test_pendiente_auditado_idempotente_y_flujo_humano_word(self):
        revision, permiso, operacion, propuesta = self.preparar(seguimiento=True)
        incorporar_plantilla(TipoRequerimiento.PRIMERO, self.abogado)
        incorporar_plantilla(TipoRequerimiento.OBSERVACIONES, self.abogado)
        evento = mcp_demo.guardar(self.abogado, revision, permiso, operacion, [propuesta])
        self.assertEqual(evento.pk, mcp_demo.guardar(self.abogado, revision, permiso, operacion, [propuesta]).pk)
        self.assertEqual(EventoRevisionInteligente.objects.filter(operacion=operacion).count(), 1)
        ver = self.client.get(f'{self.url}revisiones/{revision.pk}/').json()
        hallazgo = next(h for h in ver['analisis']['hallazgos'] if h.get('origen') == 'claude_desktop')
        self.assertEqual(hallazgo['estado'], 'PENDIENTE_REVISION')
        self.assertEqual(ver['decisiones'], [])
        aceptada = self.accion(ver, 'decisiones', accion='editar', hallazgo_id=hallazgo['id'],
                              descripcion='Observación revisada por el abogado.', fundamento='Cotejo de prueba.')
        for tipo in ('acta', 'requerimiento'):
            generado = self.accion(ver, 'generar', tipo=tipo, observaciones=[aceptada['decisiones'][0]['id']], confirmado=True)
            self.assertTrue(self.comprobar_descarga(generado['borradores'][-1]['documento']['descarga_url']).startswith(b'PK'))
        self.assertEqual(Auditoria.objects.filter(accion='revision_mcp_propuestas').count(), 1)
