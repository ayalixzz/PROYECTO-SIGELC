from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import Exact


LIMITE_ARCHIVO_PRIVADO = 20 * 1024 * 1024


class ArchivoPrivado(models.Model):
    """Contenido de archivos privados de la instalación alojada."""

    nombre = models.CharField(max_length=255, primary_key=True)
    contenido = models.BinaryField()
    tamano = models.PositiveIntegerField()

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(
                condition=models.Q(tamano__lte=LIMITE_ARCHIVO_PRIVADO),
                name='archivo_privado_tamano_permitido',
            ),
            models.CheckConstraint(
                condition=Exact(Length('contenido'), models.F('tamano')),
                name='archivo_privado_tamano_consistente',
            ),
        ]
