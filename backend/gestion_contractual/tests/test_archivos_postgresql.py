from unittest.mock import patch

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from ..archivos_modelos import ArchivoPrivado
from ..archivos_transaccionales import escritura_con_archivo
from ..models import PlantillaDocumento


@override_settings(STORAGES={
    'default': {'BACKEND': 'gestion_contractual.archivos_postgresql.AlmacenamientoPrivadoPostgreSQL'},
})
class ArchivosPostgreSQLTest(TestCase):
    def test_dos_archivos_con_el_mismo_nombre_se_conservan_privados(self):
        nombre = default_storage.save('pruebas/ejemplo-extenso.docx', ContentFile(b'primero'))
        otro = default_storage.save(nombre, ContentFile(b'segundo'), max_length=len(nombre))
        self.assertEqual(nombre, 'pruebas/ejemplo-extenso.docx')
        self.assertLessEqual(len(otro), len(nombre))
        self.assertNotEqual(nombre, otro)
        self.assertEqual(default_storage.size(nombre), 7)
        with default_storage.open(nombre) as archivo:
            self.assertEqual(archivo.read(), b'primero')
        with default_storage.open(otro) as archivo:
            self.assertEqual(archivo.read(), b'segundo')
        with self.assertRaises(NotImplementedError):
            default_storage.url(nombre)
        with self.assertRaises(NotImplementedError):
            default_storage.path(nombre)
        default_storage.delete(otro)
        self.assertTrue(default_storage.exists(nombre))
        self.assertFalse(default_storage.exists(otro))
        with self.assertRaises(FileNotFoundError):
            default_storage.open(otro)

    def test_valida_las_rutas_y_el_tamano(self):
        for nombre in ('/absoluto.docx', '../ajeno.docx', r'D:\ajeno.docx', 'sub/./archivo.docx'):
            with self.subTest(nombre=nombre), self.assertRaises(SuspiciousFileOperation):
                default_storage.save(nombre, ContentFile(b'prueba'))
        with patch('gestion_contractual.archivos_postgresql.LIMITE_ARCHIVO_PRIVADO', 3):
            with self.assertRaises(ValidationError):
                default_storage.save('exceso.docx', ContentFile(b'cuatro'))
        self.assertFalse(ArchivoPrivado.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArchivoPrivado.objects.create(nombre='incoherente.docx', contenido=b'a', tamano=2)

    def test_revierte_los_bytes_si_la_operacion_falla(self):
        campo = PlantillaDocumento().archivo
        with self.assertRaises(RuntimeError), escritura_con_archivo() as guardar:
            guardar(campo, 'plantilla.docx', b'documento ficticio')
            self.assertTrue(default_storage.exists(campo.name))
            raise RuntimeError('Fallo simulado')
        self.assertFalse(default_storage.exists(campo.name))
        with escritura_con_archivo() as guardar:
            guardar(campo, 'plantilla.docx', b'documento ficticio')
        self.assertTrue(default_storage.exists(campo.name))
