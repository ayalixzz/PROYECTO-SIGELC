"""Compensación local por directorio exclusivo; PostgreSQL y archivos no son ACID conjuntos."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from uuid import uuid4
import json, logging, os
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import transaction

actual = ContextVar('archivos_sigelc', default=None)
logger = logging.getLogger(__name__)


def referenciado(storage, name):
    from .models import DocumentoContrato, FuenteRevisionInteligente, PlantillaDocumento
    if DocumentoContrato.objects.filter(archivo=name).exists() or PlantillaDocumento.objects.filter(archivo=name).exists():
        return True
    # Existing non-demo deployments can keep running against their original schema.
    if name.startswith('revision-inteligente/'):
        return FuenteRevisionInteligente.objects.filter(archivo=name).exists()
    return False


@contextmanager
def escritura_con_archivo():
    padre = actual.get()
    archivos = []
    confirmado = [False]

    def guardar(campo, nombre, contenido):
        # El directorio se reserva exclusivamente ANTES de llamar al storage.
        # Solo ese espacio pertenece a esta operación; nunca un archivo histórico.
        ruta = PurePosixPath(campo.field.generate_filename(campo.instance, Path(nombre).name).replace('\\', '/'))
        carpeta = ruta.parent / ('operacion-' + uuid4().hex)
        storage = campo.storage
        directorio = Path(storage.path(str(carpeta)))
        directorio.parent.mkdir(parents=True, exist_ok=True)
        directorio.mkdir(exist_ok=False)
        destino = str(carpeta / ruta.name)
        journal = directorio / 'operacion-pendiente.json'
        registro = (storage, destino, directorio, journal)
        archivos.append(registro)
        journal.write_text(json.dumps({'archivo':destino,'estado':'preparando','id':carpeta.name}), encoding='utf-8')
        escrito = storage.save(destino, ContentFile(contenido))
        if escrito != destino:
            raise ValidationError('El almacenamiento cambió la ruta reservada; operación no admitida.')
        campo.name = destino
        campo._committed = True

    def limpiar(registros):
        for storage, nombre, directorio, journal in reversed(registros):
            try:
                if not directorio.exists():
                    continue
                if referenciado(storage, nombre):
                    continue
                # El contenido parcial también está en este directorio exclusivo.
                for path in directorio.iterdir():
                    if path.is_symlink() or not path.is_file():
                        raise OSError('Estructura inesperada en operación documental')
                    if path != journal:
                        storage.delete(str(PurePosixPath(nombre).parent / path.name))
                journal.unlink(missing_ok=True)
                directorio.rmdir()
            except Exception:
                logger.error('Compensación documental pendiente: %s', directorio.name)

    token = actual.set(archivos)
    try:
        # El padre conocido conserva la compensación hasta el commit real.
        with transaction.atomic(durable=padre is None):
            if padre is None:
                transaction.on_commit(lambda: confirmado.__setitem__(0, True))
            yield guardar
            if padre is not None:
                padre.extend(archivos)
            else:
                def finalizar():
                    for _, _, _, journal in archivos:
                        journal.unlink(missing_ok=True)
                transaction.on_commit(finalizar, robust=True)
    except BaseException:
        if not confirmado[0]:
            limpiar(archivos)
            logger.warning('Operación documental rechazada; archivos nuevos compensados o pendientes en su manifiesto')
        else:
            logger.warning('Operación documental confirmada; fallo posterior al commit. Se conservan sus archivos y referencias.')
        raise
    finally:
        actual.reset(token)
