from django.core.exceptions import ValidationError
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from .api_demo_datos import borrador_datos
from .api_demo_serializers import EntradaEstricta
from .api_demo_views import DemoAPIView, expediente, exigir_permiso, tiene
from .models import Auditoria, DocumentoGenerado
from .revision_claude import estado_claude, solicitar
from .revision_flujo import (
    ETAPAS, analisis_registrado, decidir, documentos_analizables, ejecutar_revision, generar_desde_revision, registrar_paquete,
)
from .revision_ia_servicio import analizar_externamente, proveedor_publico


class LimitePaquete(FileUploadHandler):
    """Bound multipart file bytes, including submissions without a reliable length."""
    limite = 44 * 1024 * 1024

    def __init__(self, request):
        super().__init__(request)
        self.recibidos = 0
        self.excedido = False

    def receive_data_chunk(self, raw_data, start):
        self.recibidos += len(raw_data)
        if self.recibidos > self.limite:
            self.excedido = True
            raise StopUpload()
        return raw_data

    def file_complete(self, file_size):
        return None


class Operacion(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()


class Paquete(Operacion):
    anterior_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)


class Decision(Operacion):
    accion = serializers.ChoiceField(choices=['aceptar', 'editar', 'rechazar', 'manual'])
    hallazgo_id = serializers.CharField(max_length=160, required=False, allow_blank=True)
    descripcion = serializers.CharField(max_length=4000, required=False, allow_blank=True)
    fundamento = serializers.CharField(max_length=2500, required=False, allow_blank=True)
    decision_esperada = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    documento_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    ubicacion = serializers.CharField(max_length=300, required=False, allow_blank=True)
    fragmento = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class Generacion(Operacion):
    tipo = serializers.ChoiceField(choices=['acta', 'requerimiento'])
    observaciones = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=80)
    confirmado = serializers.BooleanField()


class AnalisisExterno(Operacion):
    documentos = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=24)
    confirmado = serializers.BooleanField()


def validar(clase, datos):
    entrada = clase(data=datos)
    entrada.is_valid(raise_exception=True)
    return entrada.validated_data


def resumen(revision):
    return {
        'id': revision.pk, 'version': revision.version, 'fecha': revision.fecha.isoformat(),
        'anterior_id': revision.anterior_id, 'etapa': revision.etapa,
        'analizada': revision.eventos_revision.filter(tipo='analisis').exists(),
    }


def detalle(revision, usuario):
    from .revision_analisis import comparar

    analisis = analisis_registrado(revision)
    documentos = []
    for fuente in revision.fuentes_revision.order_by('pk'):
        documentos.append({
            'id': fuente.pk, 'nombre': fuente.nombre, 'sha256': fuente.sha256, 'tipo': fuente.tipo,
            'tamano': fuente.tamano, 'clasificacion': fuente.clasificacion, 'fragmentos': fuente.fragmentos,
            'advertencias': fuente.advertencias, 'procedencia': fuente.procedencia,
            'descarga_url': reverse('sigelc_demo:revision-fuente', args=[revision.contrato_id, revision.pk, fuente.pk]),
        })
    anterior = revision.anterior
    comparacion = None
    if anterior:
        previo = analisis_registrado(anterior)
        comparacion = comparar(
            documentos_analizables(anterior), documentos_analizables(revision),
            previo['hallazgos'] if previo and analisis else [], analisis['hallazgos'] if previo and analisis else [],
        )
        comparacion['hallazgos_disponibles'] = bool(previo and analisis)
    eventos = list(revision.eventos_revision.exclude(tipo='analisis').select_related('usuario'))
    generados = DocumentoGenerado.objects.in_bulk([e.datos['borrador_id'] for e in eventos if e.tipo == 'generacion'])
    actual = revision.contrato.instancias.filter(fecha_finalizacion__isnull=True).first()
    puede_decidir = tiene(usuario, 'change_documentocontrato')
    es_vigente = revision.contrato.revisiones_inteligentes.first().pk == revision.pk
    disponible = (
        actual is not None and actual.pk == revision.instancia_id
        and actual.estado_id == revision.contrato.estado_actual_id
        and revision.contrato.estado_actual.codigo == revision.etapa
        and revision.contrato.estado_actual.activo
        and ETAPAS.get(revision.etapa) == actual.plazo_dias_habiles
        and es_vigente
    )
    return {
        **resumen(revision), 'documentos': documentos, 'analisis': analisis,
        'decisiones': [{'id': e.pk, 'fecha': e.fecha.isoformat(), 'usuario': e.usuario.get_username(), **e.datos}
                       for e in eventos if e.tipo == 'decision'],
        'comparacion': comparacion,
        'puede_decidir': puede_decidir,
        'claude': estado_claude(revision, usuario, analizada=bool(analisis), es_vigente=es_vigente,
                                puede_decidir=puede_decidir),
        'proveedor_externo': proveedor_publico() if puede_decidir else None,
        'puede_generar': disponible and puede_decidir and tiene(usuario, 'add_documentocontrato'),
        'motivo_generacion': '' if disponible else 'Se necesita la última entrega de la instancia vigente de requerimiento.',
        'plazo_dias_habiles': revision.instancia.plazo_dias_habiles if revision.instancia_id else None,
        'borradores': [{**borrador_datos(generados[e.datos['borrador_id']]), 'tipo': e.datos['tipo']}
                       for e in eventos if e.tipo == 'generacion'],
    }


class RevisionView(DemoAPIView):
    def contrato(self, request, pk):
        exigir_permiso(tiene(request.user, 'view_documentocontrato'))
        return expediente(request.user, pk)

    def revision(self, request, pk, rid):
        return get_object_or_404(self.contrato(request, pk).revisiones_inteligentes, pk=rid)


class RevisionesView(RevisionView):
    parser_classes = [MultiPartParser]

    def initialize_request(self, request, *args, **kwargs):
        if request.method == 'POST':
            self.limite = LimitePaquete(request)
            request.upload_handlers.insert(0, self.limite)
        return super().initialize_request(request, *args, **kwargs)

    def get(self, request, pk):
        return Response([resumen(r) for r in self.contrato(request, pk).revisiones_inteligentes.all()])

    def post(self, request, pk):
        contrato = self.contrato(request, pk)
        exigir_permiso(tiene(request.user, 'add_documentocontrato'))
        archivos = request.FILES.getlist('archivos')
        if self.limite.excedido:
            return Response({'detail': 'El paquete supera el límite permitido.'}, status=413)
        if set(request.FILES) - {'archivos'}:
            raise ValidationError('Use el campo archivos para adjuntar el paquete.')
        datos = validar(Paquete, {k: request.data[k] for k in request.data if k not in request.FILES})
        revision = registrar_paquete(usuario=request.user, contrato_id=contrato.pk, archivos=archivos, **datos)
        return Response(detalle(revision, request.user), status=status.HTTP_201_CREATED)


class RevisionDetalleView(RevisionView):
    def get(self, request, pk, rid):
        return Response(detalle(self.revision(request, pk, rid), request.user))


class RevisionAnalisisView(RevisionView):
    def post(self, request, pk, rid):
        revision = self.revision(request, pk, rid)
        ejecutar_revision(usuario=request.user, revision=revision, **validar(Operacion, request.data))
        return Response(detalle(revision, request.user))


class RevisionIAView(RevisionView):
    def post(self, request, pk, rid):
        revision = self.revision(request, pk, rid)
        analizar_externamente(usuario=request.user, revision=revision, **validar(AnalisisExterno, request.data))
        return Response(detalle(revision, request.user))


class RevisionClaudeSolicitudView(RevisionView):
    def post(self, request, pk, rid):
        revision = self.revision(request, pk, rid)
        solicitar(usuario=request.user, revision=revision, **validar(Operacion, request.data))
        return Response(detalle(revision, request.user))


class RevisionDecisionView(RevisionView):
    def post(self, request, pk, rid):
        revision = self.revision(request, pk, rid)
        decidir(usuario=request.user, revision=revision, **validar(Decision, request.data))
        return Response(detalle(revision, request.user))


class RevisionGenerarView(RevisionView):
    def post(self, request, pk, rid):
        revision = self.revision(request, pk, rid)
        generar_desde_revision(usuario=request.user, revision=revision, **validar(Generacion, request.data))
        return Response(detalle(revision, request.user))


class RevisionFuenteView(RevisionView):
    def get(self, request, pk, rid, fid):
        revision = self.revision(request, pk, rid)
        fuente = get_object_or_404(revision.fuentes_revision, pk=fid)
        try:
            archivo = fuente.archivo.open('rb')
        except (FileNotFoundError, ValueError):
            raise Http404('Archivo no disponible.')
        try:
            Auditoria.objects.create(
                usuario=request.user, accion='descargar_fuente_revision', entidad_afectada='RevisionInteligente',
                identificador_registro=str(revision.pk), datos_nuevos={'fuente_id': fuente.pk},
            )
        except Exception:
            archivo.close()
            raise
        respuesta = FileResponse(archivo, as_attachment=True, filename=fuente.nombre, content_type='application/octet-stream')
        respuesta['X-Content-Type-Options'] = 'nosniff'
        return respuesta
