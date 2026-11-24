"""Almacenamiento privado junto a los documentos de la misma base Django."""

from pathlib import PureWindowsPath

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.core.files.utils import validate_file_name
from django.db import DEFAULT_DB_ALIAS

from .archivos_modelos import ArchivoPrivado, LIMITE_ARCHIVO_PRIVADO


class AlmacenamientoPrivadoPostgreSQL(Storage):
    """Sirve archivos mediante las vistas autenticadas de SIGELC."""

    @staticmethod
    def _nombre(name):
        name = str(name).replace('\\', '/')
        validate_file_name(name, allow_relative_path=True)
        if (
            PureWindowsPath(name).drive
            or any(part in ('', '.', '..') for part in name.split('/'))
            or any(ord(caracter) < 32 for caracter in name)
        ):
            raise SuspiciousFileOperation('El nombre del archivo privado no es válido.')
        return name

    def get_available_name(self, name, max_length=None):
        largo_campo = ArchivoPrivado._meta.pk.max_length
        if max_length is None:
            max_length = largo_campo
        nombre = super().get_available_name(self._nombre(name), max_length=min(largo_campo, max_length))
        return self._nombre(nombre)

    def _archivos(self):
        return ArchivoPrivado.objects.using(DEFAULT_DB_ALIAS)

    def _open(self, name, mode='rb'):
        if mode != 'rb':
            raise ValueError('Los archivos privados se abren en modo de lectura binaria.')
        name = self._nombre(name)
        try:
            contenido = self._archivos().values_list('contenido', flat=True).get(nombre=name)
        except ArchivoPrivado.DoesNotExist as exc:
            raise FileNotFoundError(name) from exc
        return ContentFile(bytes(contenido), name=name)

    def _save(self, name, content):
        name = self._nombre(name)
        partes = bytearray()
        for parte in content.chunks():
            if len(partes) + len(parte) > LIMITE_ARCHIVO_PRIVADO:
                raise ValidationError('El archivo no puede superar 20 MB.')
            partes.extend(parte)
        self._archivos().create(nombre=name, contenido=bytes(partes), tamano=len(partes))
        return name

    def exists(self, name):
        return self._archivos().filter(nombre=self._nombre(name)).exists()

    def delete(self, name):
        self._archivos().filter(nombre=self._nombre(name)).delete()

    def size(self, name):
        name = self._nombre(name)
        try:
            return self._archivos().values_list('tamano', flat=True).get(nombre=name)
        except ArchivoPrivado.DoesNotExist as exc:
            raise FileNotFoundError(name) from exc
