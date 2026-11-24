from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db import close_old_connections
from django.core import signals
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gestion_contractual.models import Auditoria, Contrato, DocumentoContrato, EntidadPublica, EstadoContrato, HistorialEstadoContrato, InstanciaSeguimiento, PerfilUsuario, TransicionEstado
from gestion_contractual.calendario import actualizar_calendario, calendario_disponible, calendario_nacional
from gestion_contractual.services import cambiar_estado, iniciar_seguimiento


class ContratoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.abogado = User.objects.create_user('abogado', password='test-only-password', is_staff=True)
        cls.otro = User.objects.create_user('otro', password='test-only-password', is_staff=True)
        permisos = Permission.objects.filter(content_type__app_label='gestion_contractual')
        cls.abogado.user_permissions.set(permisos)
        cls.otro.user_permissions.set(permisos)
        PerfilUsuario.objects.create(usuario=cls.abogado, abogado_operativo=True)
        cls.entidad = EntidadPublica.objects.create(nombre='Entidad de prueba', nit='test')
        cls.inicial = EstadoContrato.objects.get(codigo='primer-requerimiento')
        cls.final = EstadoContrato.objects.get(codigo='revision-abogado-1')

    def crear_contrato(self, *, iniciar=True, **kwargs):
        datos = dict(entidad=self.entidad, numero_contrato='A-1', anio_contractual=2025, objeto_contractual='Objeto de prueba', tipo_contrato='Servicio', modalidad_contratacion='Modalidad', contratista_nombre='Ejemplo', contratista_identificacion='ejemplo', valor_inicial=Decimal('100.00'), valor_actual=Decimal('100.00'), fecha_suscripcion=date(2025, 1, 1), estado_actual=self.inicial, abogado_responsable=self.abogado, supervisor_nombre='Supervisor de prueba')
        datos.update(kwargs)
        datos.setdefault('fecha_asignacion', timezone.localdate())
        contrato = Contrato.objects.create(**datos)
        if iniciar:
            contrato.instancia_prueba = iniciar_seguimiento(
                contrato_id=contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True,
            ).pk
        return contrato

    def test_numero_unico_por_anio_y_supervisor_sin_usuario(self):
        contrato = self.crear_contrato()
        self.assertEqual(contrato.supervisor_nombre, 'Supervisor de prueba')
        self.assertIsNone(contrato.supervisor_responsable)
        with self.assertRaises(ValidationError):
            self.crear_contrato()
        self.crear_contrato(anio_contractual=2026)
        otra = EntidadPublica.objects.create(nombre='Histórica', nit='otra', activo=False)
        with self.assertRaises(ValidationError):
            self.crear_contrato(entidad=otra)

    def test_abogado_unico_en_base_de_datos(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PerfilUsuario.objects.create(usuario=self.otro, abogado_operativo=True)

    def test_transicion_directa_con_motivo_confirmacion_y_auditoria(self):
        contrato = self.crear_contrato()
        registro = cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='Revisión completada', confirmado=True)
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual, self.final)
        self.assertEqual(registro.solicitado_por, self.abogado)
        self.assertEqual(registro.motivo, 'Revisión completada')
        self.assertEqual(registro.estado_anterior, self.inicial)
        self.assertTrue(Auditoria.objects.filter(accion='cambio_estado', identificador_registro=str(contrato.pk)).exists())

    def test_transicion_sin_permiso_confirmacion_motivo_o_activa(self):
        contrato = self.crear_contrato()
        args = dict(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='Revisión', confirmado=True)
        with self.assertRaises(PermissionDenied):
            cambiar_estado(**{**args, 'usuario': self.otro})
        with self.assertRaises(ValidationError):
            cambiar_estado(**{**args, 'confirmado': False})
        with self.assertRaises(ValidationError):
            cambiar_estado(**{**args, 'motivo': ' '})
        TransicionEstado.objects.update(activa=False)
        with self.assertRaises(ValidationError):
            cambiar_estado(**args)
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual, self.inicial)
        self.assertFalse(HistorialEstadoContrato.objects.exists())

    def test_historial_y_estado_no_se_modifican_directamente(self):
        contrato = self.crear_contrato()
        registro = cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='Revisión', confirmado=True)
        with self.assertRaises(ValidationError):
            HistorialEstadoContrato.objects.filter(pk=registro.pk).update(motivo='otro')
        with self.assertRaises(ValidationError):
            HistorialEstadoContrato.objects.filter(pk=registro.pk).delete()
        with self.assertRaises(ValidationError):
            Auditoria.objects.all().delete()
        with self.assertRaises(ValidationError):
            Contrato.objects.filter(pk=contrato.pk).update(estado_actual=self.inicial)

    def test_copia_antigua_no_revierte_estado(self):
        contrato = self.crear_contrato()
        copia = Contrato.objects.get(pk=contrato.pk)
        cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='Revisión', confirmado=True)
        copia.observaciones = 'Actualización'
        with self.assertRaises(ValidationError):
            copia.save()

    def test_autenticacion_y_admin_restringido(self):
        self.assertFalse(self.client.login(username='abogado', password='incorrecta'))
        self.assertTrue(self.client.login(username='abogado', password='test-only-password'))
        self.assertEqual(self.client.get(reverse('admin:gestion_contractual_contrato_changelist')).status_code, 200)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(reverse('admin:gestion_contractual_contrato_changelist')).status_code, 403)

    def test_creacion_y_edicion_admin(self):
        self.client.force_login(self.abogado)
        datos = {'entidad': self.entidad.pk, 'numero_contrato': 'B-2', 'anio_contractual': 2025, 'objeto_contractual': 'Objeto', 'tipo_contrato': 'Servicio', 'modalidad_contratacion': 'Modalidad', 'contratista_nombre': 'Ejemplo', 'contratista_identificacion': '123', 'valor_inicial': '100.00', 'valor_actual': '100.00', 'fecha_suscripcion': '2025-01-01', 'estado_actual': self.inicial.pk, 'supervisor_nombre': 'Supervisor'}
        response = self.client.post(reverse('admin:gestion_contractual_contrato_add'), datos)
        self.assertEqual(response.status_code, 302, getattr(response, 'context_data', None))
        contrato = Contrato.objects.get(numero_contrato='B-2')
        self.assertEqual(contrato.abogado_responsable, self.abogado)
        datos.pop('estado_actual')
        datos['objeto_contractual'] = 'Objeto actualizado'
        datos['version_esperada'] = contrato.version_registro
        response = self.client.post(reverse('admin:gestion_contractual_contrato_change', args=[contrato.pk]), datos)
        self.assertEqual(response.status_code, 302, getattr(response, 'context_data', None))
        contrato.refresh_from_db()
        self.assertEqual(contrato.objeto_contractual, 'Objeto actualizado')
        self.assertEqual(Auditoria.objects.filter(entidad_afectada='Contrato').count(), 2)

    def test_cambio_estado_admin_requiere_confirmacion(self):
        contrato = self.crear_contrato()
        self.client.force_login(self.abogado)
        url = reverse('admin:gestion_contractual_contrato_cambiar_estado', args=[contrato.pk])
        self.assertEqual(self.client.post(url, {'estado': self.final.pk, 'instancia_esperada': contrato.instancia_prueba, 'fecha_efectiva': timezone.localdate().isoformat(), 'motivo': 'Revisión'}).status_code, 200)
        self.assertEqual(self.client.post(url, {'estado': self.final.pk, 'instancia_esperada': contrato.instancia_prueba, 'fecha_efectiva': timezone.localdate().isoformat(), 'motivo': 'Revisión', 'confirmado': 'on'}).status_code, 302)
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual, self.final)

    def test_bloque_1_alta_y_transicion_del_catalogo(self):
        origen = EstadoContrato.objects.get(codigo='primer-requerimiento')
        destino = EstadoContrato.objects.get(codigo='revision-abogado-1')
        salto = EstadoContrato.objects.get(codigo='revision-supervisor-1')
        inactiva = EntidadPublica.objects.create(nombre='Entidad ficticia inactiva', nit='TEST-INACTIVA', activo=False)
        alta = reverse('admin:gestion_contractual_contrato_add')
        datos = {
            'entidad': self.entidad.pk, 'numero_contrato': 'PRUEBA-B1-001',
            'anio_contractual': 2026, 'objeto_contractual': 'Expediente ficticio del bloque 1',
            'tipo_contrato': 'Servicio de prueba', 'modalidad_contratacion': 'Modalidad de prueba',
            'contratista_nombre': 'Contratista ficticio', 'contratista_identificacion': 'TEST-B1',
            'valor_inicial': '100.00', 'valor_actual': '100.00',
            'fecha_suscripcion': '2026-01-01', 'estado_actual': origen.pk,
            'fecha_asignacion': timezone.localdate().isoformat(),
        }
        self.client.force_login(self.abogado)
        for cambio, campo in (({'entidad': inactiva.pk}, 'entidad'), ({'objeto_contractual': ''}, 'objeto_contractual')):
            with self.subTest(campo=campo):
                respuesta = self.client.post(alta, {**datos, **cambio})
                self.assertContains(respuesta, 'errorlist')
                self.assertIn(campo, respuesta.context['adminform'].form.errors)
        self.assertFalse(Contrato.objects.filter(numero_contrato='PRUEBA-B1-001').exists())
        self.assertEqual(self.client.post(alta, datos).status_code, 302)
        contrato = Contrato.objects.get(numero_contrato='PRUEBA-B1-001', anio_contractual=2026)
        self.assertEqual(contrato.entidad, self.entidad)
        self.assertEqual(contrato.abogado_responsable, self.abogado)
        self.assertEqual(contrato.estado_actual, origen)
        duplicado = self.client.post(alta, datos)
        self.assertContains(duplicado, 'errorlist')
        self.assertEqual(Contrato.objects.filter(numero_contrato=contrato.numero_contrato).count(), 1)

        url = reverse('admin:gestion_contractual_contrato_cambiar_estado', args=[contrato.pk])
        inicio = reverse('admin:gestion_contractual_contrato_iniciar_seguimiento', args=[contrato.pk])
        self.assertFalse(contrato.instancias.exists())
        self.assertEqual(self.client.post(inicio, {'confirmar': 'on'}).status_code, 302)
        primera = contrato.instancias.get()
        base = {'estado': destino.pk, 'motivo': 'Actuación ficticia verificada', 'confirmado': 'on',
                'instancia_esperada': primera.pk, 'fecha_efectiva': timezone.localdate().isoformat()}
        opciones = self.client.get(url).context['form'].fields['estado'].queryset
        self.assertEqual(list(opciones.values_list('pk', flat=True)), [destino.pk])
        self.client.force_login(self.otro)
        self.assertEqual(self.client.post(url, base).status_code, 403)
        self.client.force_login(self.abogado)
        for cambio in ({'motivo': ' '}, {'confirmado': ''}, {'estado': salto.pk}):
            self.assertContains(self.client.post(url, {**base, **cambio}), 'errorlist')
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual, origen)
        self.assertFalse(contrato.historial_estados.exists())
        self.assertEqual(self.client.post(url, base).status_code, 302)
        self.assertContains(self.client.post(url, base), 'errorlist')
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual, destino)
        historial = contrato.historial_estados.get()
        self.assertEqual((historial.estado_anterior, historial.estado_nuevo), (origen, destino))
        self.assertEqual(historial.solicitado_por, self.abogado)
        self.assertEqual(historial.motivo, base['motivo'])
        auditoria = Auditoria.objects.get(accion='cambio_estado', identificador_registro=str(contrato.pk))
        self.assertEqual(auditoria.datos_nuevos['historial_id'], historial.pk)

    def test_validacion_documentos(self):
        contrato = self.crear_contrato()
        def documento(nombre, contenido):
            return DocumentoContrato(contrato=contrato, tipo_documento='Soporte', nombre_original=nombre, archivo=SimpleUploadedFile(nombre, contenido), cargado_por=self.abogado)
        documento('soporte.pdf', b'contenido').full_clean()
        with self.assertRaises(ValidationError):
            documento('archivo.exe', b'contenido').full_clean()
        with self.assertRaises(ValidationError):
            documento('grande.pdf', b'x' * (20 * 1024 * 1024 + 1)).full_clean()

    def test_inicio_reingreso_y_formulario_desactualizado(self):
        asignacion = timezone.localdate() - timedelta(days=7)
        contrato = self.crear_contrato(fecha_asignacion=asignacion, fecha_suscripcion=date(2024, 1, 1))
        primera = contrato.instancias.get()
        self.assertEqual(primera.fecha_inicio, asignacion)
        self.assertEqual(primera.plazo_dias_habiles, 15)
        self.assertEqual(timezone.localdate(primera.registrada_en), timezone.localdate())
        with self.assertRaises(ValidationError):
            iniciar_seguimiento(contrato_id=contrato.pk, usuario=self.abogado, sin_actuaciones_previas=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicada = InstanciaSeguimiento(contrato=contrato, estado=self.inicial, orden=2, fecha_inicio=asignacion, usuario=self.abogado)
            duplicada._por_servicio = True
            duplicada.save(force_insert=True)

        def avanzar(destino, instancia, fecha):
            return cambiar_estado(contrato_id=contrato.pk, estado_nuevo_id=destino.pk,
                                  usuario=self.abogado, motivo='Prueba', confirmado=True,
                                  instancia_esperada=instancia.pk, fecha_efectiva=fecha)

        fecha = timezone.localdate() - timedelta(days=1)
        avanzar(self.final, primera, fecha)
        segunda = contrato.instancias.get(orden=2)
        # Esta ruta existe únicamente dentro de la transacción de esta prueba.
        TransicionEstado.objects.create(origen=self.final, destino=self.inicial)
        avanzar(self.inicial, segunda, timezone.localdate())
        tercera = contrato.instancias.get(orden=3)
        with self.assertRaises(ValidationError):
            avanzar(self.final, primera, timezone.localdate())
        self.assertEqual(contrato.instancias.filter(fecha_finalizacion__isnull=True).get(), tercera)
        vencimiento_original = primera.fecha_vencimiento
        primera.refresh_from_db()
        self.assertEqual(primera.fecha_finalizacion, fecha)
        self.assertEqual(primera.fecha_vencimiento, vencimiento_original)
        self.assertEqual(tercera.estado, self.inicial)
        self.assertEqual(tercera.fecha_inicio, timezone.localdate())
        self.assertNotEqual(tercera.fecha_vencimiento, vencimiento_original)
        contrato.refresh_from_db()
        self.assertEqual(contrato.fecha_asignacion, asignacion)

    def test_borrador_requiere_confirmacion_y_rechaza_actuaciones_previas(self):
        contrato = self.crear_contrato()
        borrador = self.crear_contrato(numero_contrato='BORRADOR', iniciar=False)
        with self.assertRaises(ValidationError):
            iniciar_seguimiento(contrato_id=borrador.pk, usuario=self.abogado, sin_actuaciones_previas=False)
        registro = HistorialEstadoContrato(contrato=borrador, estado_anterior=self.inicial, estado_nuevo=self.final,
                                           solicitado_por=self.abogado, motivo='Actuación previa')
        registro._por_servicio = True
        registro.save()
        with self.assertRaises(ValidationError):
            iniciar_seguimiento(contrato_id=borrador.pk, usuario=self.abogado, sin_actuaciones_previas=True)
        self.assertFalse(borrador.instancias.exists())
        self.assertTrue(contrato.instancias.exists())

    def test_ultimo_calendario_conservado_ante_fallo(self):
        festivos, _ = calendario_nacional(2026)
        fuente = 'https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=4954'
        version = actualizar_calendario(2026, lambda anio: (festivos, fuente))
        with self.assertRaises(ValueError):
            actualizar_calendario(2026, lambda anio: ({date(2027, 1, 1)}, fuente))
        with self.assertRaises(ValueError):
            actualizar_calendario(2026, lambda anio: ({date(2026, 1, 1)}, fuente))
        fechas, procedencia = calendario_disponible(2026)
        self.assertEqual(fechas, festivos)
        self.assertIn('PROVISIONAL', procedencia)
        self.assertEqual(version.estado, 'legacy')
        self.assertIsNone(fechas.version)

    def test_descarga_privada_autenticada_y_auditada(self):
        contrato = self.crear_contrato()
        with TemporaryDirectory() as carpeta, override_settings(MEDIA_ROOT=carpeta):
            documento = DocumentoContrato.objects.create(contrato=contrato, tipo_documento='Soporte', nombre_original='soporte.pdf', archivo=SimpleUploadedFile('soporte.pdf', b'contenido privado'), cargado_por=self.abogado)
            url = reverse('descargar_documento', args=[documento.pk])
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.force_login(self.otro)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_login(self.abogado)
            respuesta = self.client.get(url)
            self.assertEqual(respuesta.status_code, 200)
            signals.request_finished.disconnect(close_old_connections)
            try:
                self.assertEqual(b''.join(respuesta.streaming_content), b'contenido privado')
            finally:
                signals.request_finished.connect(close_old_connections)
            self.assertIn('attachment', respuesta['Content-Disposition'])
            self.assertTrue(Auditoria.objects.filter(accion='descargar_documento').exists())
