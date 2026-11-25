"""Carga de soportes, exportación y transiciones desde el expediente."""

import csv
from uuid import uuid5

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .api_demo_datos import documento_datos, estado_datos
from .api_demo_serializers import EntradaEstricta, FiltrosContrato
from .api_demo_views import DemoAPIView, detalle_datos, expediente, exigir_permiso, filtrar_expedientes, tiene
from .api_revision_inteligente import LimitePaquete
from .archivos_transaccionales import escritura_con_archivo
from .competencia import fecha_confirmada
from .models import TipoDocumental, TransicionEstado
from .revision import recibir_documento
from .services import cambiar_estado


class RecepcionEntrada(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()
    categoria_id = serializers.IntegerField(min_value=1, required=False)
    fecha_recepcion = serializers.DateField(allow_null=True, required=False, default=None)
    descripcion = serializers.CharField(max_length=4000, allow_blank=True, required=False, default='')


class CargaSoportesView(DemoAPIView):
    parser_classes = [MultiPartParser]

    def get(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_documentocontrato', 'add_documentocontrato'))
        expediente(request.user, pk)
        return Response(list(TipoDocumental.objects.filter(activo=True).order_by('nombre').values('id', 'nombre')))

    def initialize_request(self, request, *args, **kwargs):
        if request.method == 'POST':
            self.limite = LimitePaquete(request)
            request.upload_handlers.insert(0, self.limite)
        return super().initialize_request(request, *args, **kwargs)

    def post(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_documentocontrato', 'add_documentocontrato'))
        contrato = expediente(request.user, pk)
        archivos = request.FILES.getlist('archivos')
        if self.limite.excedido:
            return Response({'detail': 'El lote supera el límite de carga.'}, status=413)
        if set(request.FILES) != {'archivos'} or not 1 <= len(archivos) <= 12:
            raise serializers.ValidationError({'archivos': 'Seleccione entre uno y doce archivos PDF, DOCX o XLSX.'})
        entrada = RecepcionEntrada(data={k: request.data[k] for k in request.data if k not in request.FILES})
        entrada.is_valid(raise_exception=True)
        datos = dict(entrada.validated_data)
        operacion = datos.pop('operacion')
        categoria_id = datos.pop('categoria_id', None)
        categoria = get_object_or_404(TipoDocumental, pk=categoria_id, activo=True) if categoria_id else None
        # La compensación y el guardado son atómicos para el lote completo.
        with escritura_con_archivo():
            documentos = [
                recibir_documento(usuario=request.user, contrato_id=contrato.pk, categoria=categoria,
                                  archivo=archivo, operacion=uuid5(operacion, str(indice)), **datos)
                for indice, archivo in enumerate(archivos)
            ]
        return Response([documento_datos(d) for d in documentos], status=status.HTTP_201_CREATED)


class TransicionEntrada(EntradaEstricta, serializers.Serializer):
    estado_nuevo_id = serializers.IntegerField(min_value=1)
    instancia_esperada = serializers.IntegerField(min_value=1)
    motivo = serializers.CharField(max_length=4000)
    confirmado = serializers.BooleanField()
    fecha_efectiva = serializers.DateField()
    documentos_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=24,
                                            required=False, default=list)


def _transiciones(contrato):
    return TransicionEstado.objects.filter(
        origen=contrato.estado_actual, activa=True, origen__activo=True, destino__activo=True,
    ).exclude(destino__codigo__in=('liquidado', 'cierre'))


class TransicionesContratoView(DemoAPIView):
    def get(self, request, pk):
        exigir_permiso(tiene(request.user, 'change_contrato'))
        contrato = expediente(request.user, pk)
        instancia = contrato.instancias.filter(fecha_finalizacion__isnull=True, estado=contrato.estado_actual).first()
        transiciones = _transiciones(contrato).select_related('destino').order_by('destino__orden', 'destino__pk')
        return Response({
            'instancia': instancia.pk if instancia else None,
            'hoy': timezone.localdate(),
            'estados': [estado_datos(t.destino) for t in transiciones] if instancia else [],
        })

    def post(self, request, pk):
        exigir_permiso(tiene(request.user, 'change_contrato'))
        contrato = expediente(request.user, pk)
        entrada = TransicionEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        datos = entrada.validated_data
        if not _transiciones(contrato).filter(destino_id=datos['estado_nuevo_id']).exists():
            raise serializers.ValidationError({'estado_nuevo_id': 'La transición no está habilitada para el estado vigente.'})
        cambiar_estado(contrato_id=contrato.pk, usuario=request.user, **datos)
        contrato.refresh_from_db()
        return Response(detalle_datos(contrato, request.user))


def _celda(valor):
    if valor is None:
        return ''
    if isinstance(valor, str) and (valor.lstrip().startswith(('=', '+', '-', '@')) or valor.startswith(('\t', '\r', '\n'))):
        return "'" + valor
    return valor


class ExportarContratosView(DemoAPIView):
    def get(self, request):
        entrada = FiltrosContrato(data=request.query_params)
        entrada.is_valid(raise_exception=True)
        contratos = filtrar_expedientes(request.user, entrada.validated_data)
        ver_competencia = tiene(request.user, 'view_historialestadocontrato')
        respuesta = HttpResponse(content_type='text/csv; charset=utf-8',
                                 headers={'Content-Disposition': 'attachment; filename="sigelc-contratos.csv"'})
        respuesta.write('\ufeff')
        escritor = csv.writer(respuesta, delimiter=';')
        escritor.writerow(['Número', 'Vigencia', 'Entidad', 'Tipo de contrato', 'Modalidad', 'Contratista',
                           'Identificación', 'Supervisor', 'Cargo del supervisor', 'Objeto', 'Valor inicial',
                           'Valor adicional', 'Valor total', 'Suscripción', 'Inicio', 'Terminación', 'Asignación',
                           'Estado', 'Vencimiento operativo registrado', 'Fecha de competencia verificada'])
        for contrato in contratos.prefetch_related('instancias'):
            instancia = next((etapa for etapa in contrato.instancias.all() if etapa.fecha_finalizacion is None), None)
            escritor.writerow(map(_celda, [
                contrato.numero_contrato, contrato.anio_contractual, contrato.entidad.nombre, contrato.tipo_contrato,
                contrato.modalidad_contratacion, contrato.contratista_nombre, contrato.contratista_identificacion,
                contrato.supervisor_nombre, contrato.supervisor_cargo, contrato.objeto_contractual,
                contrato.valor_inicial, contrato.valor_adicional, contrato.valor_actual,
                contrato.fecha_suscripcion, contrato.fecha_inicio, contrato.fecha_terminacion, contrato.fecha_asignacion,
                contrato.estado_actual.nombre, instancia.fecha_vencimiento if instancia else None,
                fecha_confirmada(contrato) if ver_competencia else None,
            ]))
        return respuesta
