"""Consulta y registro de alertas del expediente autenticado."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from .alertas import ficha_alertas, registrar_hitos, registrar_prioridad_temporal
from .api_demo_serializers import EntradaEstricta
from .api_demo_views import DemoAPIView, con_seguimiento_activo, expedientes, exigir_permiso, tiene
from .calendarios_servicios import bloquear_calendarios


class PrioridadTemporalEntrada(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()
    motivo = serializers.CharField(max_length=1000)
    vence = serializers.DateField()


def _etapa(contrato):
    return next(iter(contrato.etapas_abiertas), None)


def _contratos(usuario, *, bloquear=False):
    contratos = expedientes(usuario).order_by('pk')
    if bloquear:
        contratos = contratos.select_for_update(of=('self',))
    return con_seguimiento_activo(contratos)


def _ficha(contrato, usuario, hoy):
    return ficha_alertas(
        contrato, hoy, _etapa(contrato), puede_registrar=tiene(usuario, 'change_contrato'),
        ver_soportes=tiene(usuario, 'view_documentocontrato'),
    )


class AlertasContratoView(DemoAPIView):
    @transaction.atomic
    def get(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_historialestadocontrato'))
        bloquear_calendarios()
        contrato = get_object_or_404(_contratos(request.user), pk=pk)
        return Response(_ficha(contrato, request.user, timezone.localdate()))


class RegistrarAlertasView(DemoAPIView):
    @transaction.atomic
    def post(self, request, pk=None):
        exigir_permiso(tiene(request.user, 'view_historialestadocontrato', 'change_contrato'))
        if request.data:
            raise serializers.ValidationError('La fecha y los hitos se obtienen del expediente.')
        bloquear_calendarios()
        hoy = timezone.localdate()
        contratos = _contratos(request.user, bloquear=True)
        if pk is not None:
            contratos = [get_object_or_404(contratos, pk=pk)]
        cantidad = sum(registrar_hitos(c, request.user, hoy, _etapa(c)) for c in contratos)
        return Response({'registradas': cantidad, 'fecha': hoy.isoformat()})


class PrioridadTemporalView(DemoAPIView):
    @transaction.atomic
    def post(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_historialestadocontrato', 'change_contrato'))
        entrada = PrioridadTemporalEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        bloquear_calendarios()
        contrato = get_object_or_404(_contratos(request.user, bloquear=True), pk=pk)
        hoy = timezone.localdate()
        registrar_prioridad_temporal(contrato, request.user, hoy, **entrada.validated_data)
        return Response(_ficha(contrato, request.user, hoy))
