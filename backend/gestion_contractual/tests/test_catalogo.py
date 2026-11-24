from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from tempfile import TemporaryDirectory

from .test_contratos import ContratoTest
from gestion_contractual.models import Auditoria, DocumentoContrato, EstadoContrato, HistorialEstadoContrato, SoporteCambioEstado, TransicionEstado
from gestion_contractual.services import cambiar_estado


class CatalogoYSoportesTest(ContratoTest):
    def test_catalogo_idempotente(self):
        modulo = import_module('gestion_contractual.migrations.0006_catalogo_seguimiento')
        total = EstadoContrato.objects.count()
        modulo.cargar_estados(apps, SimpleNamespace(connection=connection))
        modulo.cargar_estados(apps, SimpleNamespace(connection=connection))
        self.assertEqual(EstadoContrato.objects.count(), total)
        self.assertEqual(EstadoContrato.objects.get(codigo='primer-requerimiento').plazo_dias_habiles, 15)
        self.assertIsNone(EstadoContrato.objects.get(codigo='liquidado').plazo_dias_habiles)

    def test_matriz_aprobada_idempotente_sin_saltos(self):
        modulo = import_module('gestion_contractual.migrations.0007_transiciones_aprobadas')
        modulo.crear_transiciones(apps, SimpleNamespace(connection=connection))
        modulo.crear_transiciones(apps, SimpleNamespace(connection=connection))
        ids = EstadoContrato.objects.filter(codigo__in=modulo.SECUENCIA).values_list('id', flat=True)
        pares = set(TransicionEstado.objects.filter(origen_id__in=ids, activa=True).values_list('origen__codigo', 'destino__codigo'))
        self.assertEqual(pares, set(zip(modulo.SECUENCIA, modulo.SECUENCIA[1:])))
        self.assertFalse(TransicionEstado.objects.filter(destino__codigo__in=['liquidado', 'cierre'], activa=True).exists())

    def test_soporte_historial_y_sin_cambios_posteriores(self):
        contrato = self.crear_contrato()
        with TemporaryDirectory() as carpeta, override_settings(MEDIA_ROOT=carpeta):
            documento = DocumentoContrato.objects.create(contrato=contrato, tipo_documento='Soporte', nombre_original='archivo.pdf', archivo=SimpleUploadedFile('archivo.pdf', b'pdf'), cargado_por=self.abogado)
            historial = cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='Con soporte', confirmado=True, observaciones='Nota', documentos_ids=[documento.pk])
            self.assertEqual(historial.observaciones, 'Nota')
            self.assertEqual(historial.soportes.get().documento, documento)
            with self.assertRaises(ValidationError):
                SoporteCambioEstado.objects.filter(historial=historial).delete()
            instancia = contrato.instancias.get(evento_origen=historial)
            self.assertEqual(historial.fecha_inicio_plazo, instancia.fecha_inicio)
            self.assertEqual(historial.fecha_vencimiento_plazo, instancia.fecha_vencimiento)

    def test_soporte_ajeno_y_rollback_de_auditoria(self):
        contrato = self.crear_contrato()
        otro = self.crear_contrato(numero_contrato='OTRO')
        with TemporaryDirectory() as carpeta, override_settings(MEDIA_ROOT=carpeta):
            documento = DocumentoContrato.objects.create(contrato=otro, tipo_documento='Soporte', nombre_original='archivo.pdf', archivo=SimpleUploadedFile('archivo.pdf', b'pdf'), cargado_por=self.abogado)
            with self.assertRaises(ValidationError):
                cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='x', confirmado=True, documentos_ids=[documento.pk])
            with patch.object(Auditoria.objects, 'create', side_effect=RuntimeError('error simulado')):
                with self.assertRaises(RuntimeError):
                    cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=self.final.pk, usuario=self.abogado, motivo='x', confirmado=True)
            contrato.refresh_from_db()
            self.assertEqual(contrato.estado_actual_id, self.inicial.pk)
            self.assertFalse(HistorialEstadoContrato.objects.filter(contrato=contrato).exists())
            instancia = contrato.instancias.get()
            self.assertEqual(instancia.pk, contrato.instancia_prueba)
            self.assertIsNone(instancia.fecha_finalizacion)

    def test_liquidacion_bloqueada_aunque_exista_transicion(self):
        contrato = self.crear_contrato()
        liquidado = EstadoContrato.objects.get(codigo='liquidado')
        TransicionEstado.objects.create(origen=self.inicial, destino=liquidado, activa=True)
        with self.assertRaises(ValidationError):
            cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=liquidado.pk, usuario=self.abogado, motivo='x', confirmado=True)
        contrato.refresh_from_db()
        self.assertEqual(contrato.estado_actual_id, self.inicial.pk)

    def test_no_se_crea_en_liquidacion_o_cierre(self):
        for codigo in ('liquidado', 'cierre'):
            with self.subTest(codigo=codigo), self.assertRaises(ValidationError):
                self.crear_contrato(estado_actual=EstadoContrato.objects.get(codigo=codigo))

    def test_cierre_bloqueado_aunque_exista_transicion(self):
        contrato = self.crear_contrato()
        cierre = EstadoContrato.objects.get(codigo='cierre')
        TransicionEstado.objects.create(origen=self.inicial, destino=cierre, activa=True)
        with self.assertRaises(ValidationError):
            cambiar_estado(contrato_id=contrato.pk, instancia_esperada=contrato.instancia_prueba, estado_nuevo_id=cierre.pk, usuario=self.abogado, motivo='x', confirmado=True)
