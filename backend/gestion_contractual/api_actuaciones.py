"""API de tareas y radicados para el abogado titular del expediente."""
from collections.abc import Mapping

from django.db.models import Prefetch
from rest_framework import serializers, status
from rest_framework.response import Response

from .actuaciones import registrar_actuacion
from .actuaciones_models import ActuacionContractual, EventoActuacionContractual
from .alertas import ultimo_memorando
from .api_demo_serializers import EntradaEstricta
from .api_demo_views import DemoAPIView, expediente, exigir_permiso, tiene


class OperacionActuacion(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()
    documento_id = serializers.IntegerField(min_value=1, allow_null=True, default=None)


class Planificar(OperacionActuacion):
    accion = serializers.ChoiceField(choices=['planificar'])
    titulo = serializers.CharField(max_length=250)
    descripcion = serializers.CharField(max_length=4000, allow_blank=True, default='')
    vence = serializers.DateField(allow_null=True, default=None)


class Memorando(OperacionActuacion):
    accion = serializers.ChoiceField(choices=['memorando'])
    titulo = serializers.CharField(max_length=250)
    descripcion = serializers.CharField(max_length=4000, allow_blank=True, default='')
    radicado = serializers.CharField(max_length=120)
    fecha = serializers.DateField()


class CerrarTarea(OperacionActuacion):
    accion = serializers.ChoiceField(choices=EventoActuacionContractual.Accion.choices)
    actuacion_id = serializers.IntegerField(min_value=1)
    fecha = serializers.DateField()
    descripcion = serializers.CharField(max_length=4000)


def _comunes(registro, ver_soportes):
    return {
        'id': registro.pk, 'fecha': registro.fecha.isoformat(), 'descripcion': registro.descripcion,
        'documento_id': registro.documento_id if ver_soportes else None,
        'usuario': registro.usuario.get_username(), 'registrado_en': registro.registrado_en.isoformat(),
    }


def _listar(contrato, usuario):
    ver_soportes = tiene(usuario, 'view_documentocontrato')
    registros = contrato.actuaciones.select_related('usuario').prefetch_related(
        Prefetch('eventos', queryset=EventoActuacionContractual.objects.select_related('usuario')),
    )
    salida = []
    for registro in registros:
        eventos = [{**_comunes(e, ver_soportes), 'accion': e.accion} for e in registro.eventos.all()]
        estado = 'registrado' if registro.tipo == ActuacionContractual.Tipo.MEMORANDO else 'pendiente'
        if eventos:
            estado = 'realizada' if eventos[0]['accion'] == EventoActuacionContractual.Accion.REALIZAR else 'cancelada'
        salida.append({
            **_comunes(registro, ver_soportes), 'tipo': registro.tipo, 'titulo': registro.titulo,
            'radicado': registro.radicado, 'vence': registro.vence.isoformat() if registro.vence else None,
            'estado': estado, 'eventos': eventos,
        })
    return {
        'actuaciones': salida, 'puede_registrar': tiene(usuario, 'change_contrato'),
        'ultimo_memorando': ultimo_memorando(contrato, ver_soportes=ver_soportes),
    }


class ActuacionesView(DemoAPIView):
    def get(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_historialestadocontrato'))
        return Response(_listar(expediente(request.user, pk), request.user))

    def post(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_historialestadocontrato', 'change_contrato'))
        contrato = expediente(request.user, pk)
        mapa = {'planificar': Planificar, 'memorando': Memorando, 'realizar': CerrarTarea, 'cancelar': CerrarTarea}
        accion = request.data.get('accion') if isinstance(request.data, Mapping) else None
        clase = mapa.get(accion) if isinstance(accion, str) else None
        if clase is None:
            raise serializers.ValidationError({'accion': 'Seleccione planificar, memorando, realizar o cancelar.'})
        entrada = clase(data=request.data)
        entrada.is_valid(raise_exception=True)
        registrar_actuacion(usuario=request.user, contrato_id=contrato.pk, **entrada.validated_data)
        return Response(_listar(contrato, request.user), status=status.HTTP_201_CREATED)
