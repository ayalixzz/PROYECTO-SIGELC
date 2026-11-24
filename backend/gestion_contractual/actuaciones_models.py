"""Tareas y memorandos con trazabilidad, ajenos a las transiciones contractuales."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .models import RegistroInmutableQuerySet


class RegistroActuacionInmutable(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    documento = models.ForeignKey('gestion_contractual.DocumentoContrato', on_delete=models.PROTECT, blank=True, null=True)
    descripcion = models.TextField(blank=True, max_length=4000)
    fecha = models.DateField()
    registrado_en = models.DateTimeField(auto_now_add=True)
    operacion = models.UUIDField(unique=True)
    huella = models.CharField(max_length=64)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not getattr(self, '_por_servicio', False) or not self._state.adding or self.pk:
            raise ValidationError('Las actuaciones solo pueden incorporarse por el servicio de registro.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('El historial de actuaciones no puede eliminarse.')


class ActuacionContractual(RegistroActuacionInmutable):
    class Tipo(models.TextChoices):
        TAREA = 'tarea', 'Tarea'
        MEMORANDO = 'memorando', 'Memorando o radicado'

    contrato = models.ForeignKey('gestion_contractual.Contrato', on_delete=models.PROTECT, related_name='actuaciones')
    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    titulo = models.CharField(max_length=250)
    vence = models.DateField(blank=True, null=True)
    radicado = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-fecha', '-pk']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(tipo='tarea', radicado='')
                    | (models.Q(tipo='memorando', vence__isnull=True) & ~models.Q(radicado=''))
                ),
                name='actuacion_campos_por_tipo',
            ),
            models.CheckConstraint(condition=~models.Q(titulo=''), name='actuacion_titulo_no_vacio'),
        ]


class EventoActuacionContractual(RegistroActuacionInmutable):
    class Accion(models.TextChoices):
        REALIZAR = 'realizar', 'Registrar realización'
        CANCELAR = 'cancelar', 'Cancelar tarea'

    actuacion = models.ForeignKey(ActuacionContractual, on_delete=models.PROTECT, related_name='eventos')
    accion = models.CharField(max_length=12, choices=Accion.choices)

    class Meta:
        ordering = ['pk']
        constraints = [
            models.UniqueConstraint(fields=['actuacion'], name='actuacion_un_evento_terminal'),
            models.CheckConstraint(condition=models.Q(accion__in=['realizar', 'cancelar']), name='actuacion_evento_valido'),
            models.CheckConstraint(condition=~models.Q(descripcion=''), name='actuacion_evento_con_descripcion'),
        ]
