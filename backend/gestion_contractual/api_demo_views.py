from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError as ValidacionAPI
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, BasePermission, SAFE_METHODS
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import es_abogado_operativo
from .alertas import datos_expediente, evaluar_expediente, prioridad_efectiva, resumen_panel
from .api_demo_datos import (
    borrador_datos, contrato_datos, documento_datos, entidad_datos,
    estado_datos, historial_datos, seguimiento_datos, vencimiento_datos,
)
from .api_demo_serializers import (
    ContratoEdicionEntrada, ContratoEntrada, FiltrosContrato,
    GeneracionEntrada, InicioSeguimientoEntrada, SesionEntrada,
)
from .calendarios_servicios import bloquear_calendarios
from .integridad import ConflictoEdicion
from .models import Contrato, EntidadPublica, EstadoContrato, InstanciaSeguimiento, TipoRequerimiento
from .primer_requerimiento import preparar_primer_requerimiento
from .requerimientos import generar_requerimiento
from .requerimientos_sucesivos import preparar_requerimiento_sucesivo
from .services import guardar_contrato, iniciar_seguimiento, validar_inicio_seguimiento
from .views import descargar_documento


def tiene(usuario, *permisos):
    return usuario.has_perms([f'gestion_contractual.{permiso}' for permiso in permisos])


class EsAbogadoDemo(BasePermission):
    message = 'Inicie sesión con el abogado operativo autorizado.'

    def has_permission(self, request, view):
        return es_abogado_operativo(request.user) and tiene(request.user, 'view_contrato')


class ConflictoAPI(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'conflicto'


class DemoAPIView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [EsAbogadoDemo]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        add_never_cache_headers(response)
        patch_vary_headers(response, ['Cookie'])
        return response

    def handle_exception(self, exc):
        if isinstance(exc, ConflictoEdicion):
            exc = ConflictoAPI(exc.messages[0])
        elif isinstance(exc, ValidationError):
            exc = ValidacionAPI(exc.message_dict if hasattr(exc, 'error_dict') else {'detail': ' '.join(exc.messages)})
        elif isinstance(exc, IntegrityError):
            restriccion = getattr(getattr(exc.__cause__, 'diag', None), 'constraint_name', None)
            if restriccion in {'contrato_entidad_anio_numero_unico', 'contrato_anio_numero_global_unico'}:
                exc = ConflictoAPI('Ya existe un contrato con ese número y vigencia.')
            elif restriccion == 'gestion_contractual_documentogenerado_operacion_key':
                exc = ConflictoAPI('Identificador de operación en uso.')
        return super().handle_exception(exc)


class SesionView(DemoAPIView):
    permission_classes = [AllowAny]

    def _datos(self, request):
        usuario = request.user
        autorizado = es_abogado_operativo(usuario) and tiene(usuario, 'view_contrato')
        return {
            'usuario': {
                'id': usuario.pk,
                'username': usuario.get_username(),
                'nombre': usuario.get_full_name() or usuario.get_username(),
            } if autorizado else None,
            'csrfToken': get_token(request._request),
        }

    def get(self, request):
        return Response(self._datos(request))

    def post(self, request):
        # SessionAuthentication solo verifica CSRF automáticamente si hay sesión.
        # El inicio de sesión exige la comprobación también para usuarios anónimos.
        SessionAuthentication().enforce_csrf(request)
        entrada = SesionEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        usuario = authenticate(request._request, **entrada.validated_data)
        if not usuario or not es_abogado_operativo(usuario) or not tiene(usuario, 'view_contrato'):
            raise PermissionDenied('Usuario o contraseña incorrectos, o cuenta sin acceso a SIGELC.')
        login(request._request, usuario)
        request.user = usuario
        return Response(self._datos(request))

    def delete(self, request):
        SessionAuthentication().enforce_csrf(request)
        logout(request._request)
        request.user = request._request.user
        return Response(self._datos(request))


def expedientes(usuario):
    return Contrato.objects.filter(abogado_responsable=usuario).select_related('entidad', 'estado_actual')


def expediente(usuario, pk):
    return get_object_or_404(expedientes(usuario), pk=pk)


def con_seguimiento_activo(queryset):
    activas = InstanciaSeguimiento.objects.filter(fecha_finalizacion__isnull=True)
    return queryset.prefetch_related(Prefetch('instancias', queryset=activas, to_attr='etapas_abiertas'))


def ver_documentos(usuario):
    return tiene(usuario, 'view_documentocontrato')


def ver_historial(usuario):
    return tiene(usuario, 'view_historialestadocontrato')


def puede_generar(usuario):
    return tiene(usuario, 'view_documentocontrato', 'add_documentocontrato')


def exigir_permiso(condicion):
    if not condicion:
        raise PermissionDenied('No tiene permiso para esta operación.')


def documentos_datos(contrato):
    return [documento_datos(documento) for documento in contrato.documentos.filter(activo=True).order_by('-fecha_carga', '-pk')]


def instancias_datos(contrato):
    return [(instancia, vencimiento_datos(instancia)) for instancia in contrato.instancias.select_related('estado')]


def detalle_datos(contrato, usuario):
    instancias = instancias_datos(contrato)
    vence = next((vencimiento for instancia, vencimiento in instancias if not instancia.fecha_finalizacion), None)
    editable = tiene(usuario, 'change_contrato')
    puede_iniciar = False
    if editable and not instancias:
        try:
            validar_inicio_seguimiento(contrato)
        except ValidationError:
            pass
        else:
            puede_iniciar = True
    advertencias = []
    if not instancias:
        advertencias.append('El expediente todavía no tiene seguimiento iniciado.')
    if vence and vence['advertencia']:
        advertencias.append(vence['advertencia'])
    return {
        **contrato_datos(contrato, vence),
        'puede_editar_fecha_asignacion': editable and not instancias,
        'seguimiento': [seguimiento_datos(instancia, vencimiento) for instancia, vencimiento in instancias],
        'historial': historial_datos(contrato) if ver_historial(usuario) else [],
        'documentos': documentos_datos(contrato) if ver_documentos(usuario) else [],
        'advertencias': advertencias,
        'permisos': {
            'editar': editable,
            'ver_documentos': ver_documentos(usuario),
            'ver_historial': ver_historial(usuario),
            'generar': puede_generar(usuario),
            'iniciar_seguimiento': puede_iniciar,
        },
    }


class CatalogosView(DemoAPIView):
    def get(self, request):
        return Response({
            'estados': [estado_datos(estado) for estado in EstadoContrato.objects.all()],
            'entidad': entidad_datos(EntidadPublica.objects.filter(activo=True).first()),
            'permisos': {'crear': tiene(request.user, 'add_contrato')},
        })


class PaginacionContratos(PageNumberPagination):
    page_size = 25


class ContratosView(DemoAPIView):
    @transaction.atomic
    def get(self, request):
        filtros = FiltrosContrato(data=request.query_params)
        filtros.is_valid(raise_exception=True)
        datos = filtros.validated_data
        ordenados = expedientes(request.user).order_by('-anio_contractual', 'numero_contrato', 'pk')
        if datos.get('buscar'):
            buscar = datos['buscar']
            ordenados = ordenados.filter(Q(numero_contrato__icontains=buscar) |
                Q(contratista_nombre__icontains=buscar) | Q(contratista_identificacion__icontains=buscar) |
                Q(objeto_contractual__icontains=buscar))
        if 'anio' in datos:
            ordenados = ordenados.filter(anio_contractual=datos['anio'])
        if 'estado' in datos:
            ordenados = ordenados.filter(estado_actual__codigo=datos['estado'])
        bloquear_calendarios()
        paginador = PaginacionContratos()
        contratos = paginador.paginate_queryset(con_seguimiento_activo(ordenados), request)
        return paginador.get_paginated_response([
            contrato_datos(contrato, vencimiento_datos(next(iter(contrato.etapas_abiertas), None)))
            for contrato in contratos
        ])

    @transaction.atomic
    def post(self, request):
        exigir_permiso(tiene(request.user, 'add_contrato'))
        entrada = ContratoEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        entidad = EntidadPublica.objects.filter(activo=True).first()
        inicial = EstadoContrato.objects.filter(codigo=TipoRequerimiento.PRIMERO, activo=True).first()
        if not entidad or not inicial:
            raise ConflictoAPI('Configure la entidad y el estado inicial antes de crear un expediente.')
        contrato = Contrato(entidad=entidad, estado_actual=inicial, **entrada.validated_data)
        guardar_contrato(contrato=contrato, usuario=request.user)
        return Response(detalle_datos(contrato, request.user), status=status.HTTP_201_CREATED)


class ContratoView(DemoAPIView):
    @transaction.atomic
    def get(self, request, pk):
        bloquear_calendarios()
        return Response(detalle_datos(expediente(request.user, pk), request.user))

    @transaction.atomic
    def patch(self, request, pk):
        exigir_permiso(tiene(request.user, 'change_contrato'))
        contrato = expediente(request.user, pk)
        entrada = ContratoEdicionEntrada(contrato, data=request.data, partial=True)
        entrada.is_valid(raise_exception=True)
        datos = dict(entrada.validated_data)
        contrato._version_esperada = datos.pop('version_registro')
        for campo, valor in datos.items():
            setattr(contrato, campo, valor)
        guardar_contrato(contrato=contrato, usuario=request.user, campos_editados=datos)
        return Response(detalle_datos(contrato, request.user))


class SeguimientoView(DemoAPIView):
    @transaction.atomic
    def post(self, request, pk):
        exigir_permiso(tiene(request.user, 'change_contrato'))
        contrato = expediente(request.user, pk)
        entrada = InicioSeguimientoEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        iniciar_seguimiento(
            contrato_id=contrato.pk, usuario=request.user,
            sin_actuaciones_previas=entrada.validated_data['confirmado'],
        )
        return Response(detalle_datos(contrato, request.user), status=status.HTTP_201_CREATED)


class VencimientosView(DemoAPIView):
    @transaction.atomic
    def get(self, request, pk):
        bloquear_calendarios()
        return Response([
            seguimiento_datos(instancia, vencimiento)
            for instancia, vencimiento in instancias_datos(expediente(request.user, pk))
        ])


class HistorialView(DemoAPIView):
    def get(self, request, pk):
        exigir_permiso(ver_historial(request.user))
        return Response(historial_datos(expediente(request.user, pk)))


class DocumentosView(DemoAPIView):
    def get(self, request, pk):
        exigir_permiso(ver_documentos(request.user))
        return Response(documentos_datos(expediente(request.user, pk)))


class DescargarView(DemoAPIView):
    def get(self, request, pk, documento_id):
        exigir_permiso(ver_documentos(request.user))
        contrato = expediente(request.user, pk)
        documento = get_object_or_404(contrato.documentos, pk=documento_id, activo=True)
        return descargar_documento(request._request, documento.pk)


class RequerimientoView(DemoAPIView):
    tipo = TipoRequerimiento.PRIMERO

    @transaction.atomic
    def get(self, request, pk):
        exigir_permiso(puede_generar(request.user))
        bloquear_calendarios()
        contrato = expediente(request.user, pk)
        versiones = contrato.borradores_requerimiento.filter(tipo=self.tipo).select_related('documento').order_by('-version')
        respuesta = {'disponible': False, 'motivo': None, 'campos': [], 'versiones': [borrador_datos(v) for v in versiones]}
        try:
            if contrato.estado_actual.codigo != self.tipo or not contrato.estado_actual.activo:
                raise ValidationError('El requerimiento corresponde a otra etapa del expediente.')
            campos = self.preparar(usuario=request.user, contrato_id=contrato.pk)
        except ValidationError as error:
            respuesta['motivo'] = ' '.join(error.messages)
        except FileNotFoundError:
            respuesta['motivo'] = 'El archivo de la plantilla no está disponible.'
        else:
            respuesta['disponible'] = True
            respuesta['campos'] = [
                {'nombre': nombre, 'valor': valor, 'fuente': fuente, 'pendiente': valor is None or valor == '' or valor == []}
                for nombre, valor, fuente in campos
            ]
        return Response(respuesta)

    def preparar(self, **datos):
        if self.tipo == TipoRequerimiento.PRIMERO:
            return preparar_primer_requerimiento(**datos)
        return preparar_requerimiento_sucesivo(tipo=self.tipo, **datos)

    def post(self, request, pk):
        exigir_permiso(puede_generar(request.user))
        contrato = expediente(request.user, pk)
        entrada = GeneracionEntrada(data=request.data)
        entrada.is_valid(raise_exception=True)
        operacion = entrada.validated_data['operacion']
        try:
            borrador = generar_requerimiento(
                usuario=request.user, contrato_id=contrato.pk, tipo=self.tipo, operacion=operacion, exigir_etapa_vigente=True,
            )
        except FileNotFoundError as error:
            raise ConflictoAPI('El archivo de la plantilla no está disponible.') from error
        return Response(borrador_datos(borrador), status=status.HTTP_201_CREATED)


class DashboardView(DemoAPIView):
    @transaction.atomic
    def get(self, request):
        bloquear_calendarios()
        hoy = timezone.localdate()
        contratos = con_seguimiento_activo(expedientes(request.user).order_by('-fecha_actualizacion', '-pk'))
        total = activos = pendientes = vencidos = 0
        proximos = []
        atencion = []
        alertas = []
        revisiones = []
        prioridades = []
        ver_alertas = tiene(request.user, 'view_historialestadocontrato')
        for contrato in contratos:
            total += 1
            if contrato.estado_actual.codigo in ('liquidado', 'cierre'):
                continue
            pendientes += 1
            abierta = next(iter(contrato.etapas_abiertas), None)
            activos += bool(abierta)
            vencimiento = vencimiento_datos(abierta)
            if ver_alertas:
                alertas_contrato, revisiones_contrato, _ = evaluar_expediente(contrato, hoy, abierta, vencimiento)
                alertas.extend(alertas_contrato)
                revisiones.extend(revisiones_contrato)
                prioridad = prioridad_efectiva(contrato, hoy, alertas_contrato)
                if prioridad['origen'] == 'manual':
                    prioridades.append({**datos_expediente(contrato), **prioridad})
            razon = None
            if not abierta:
                razon = 'Seguimiento por iniciar.'
            elif vencimiento and vencimiento['estado'] != 'confirmado':
                razon = vencimiento['advertencia']
            elif vencimiento:
                fecha = vencimiento['fecha']
                if fecha < hoy.isoformat():
                    vencidos += 1
                    razon = 'Vencimiento operativo cumplido.'
                elif fecha <= (hoy + timedelta(days=30)).isoformat():
                    proximos.append(contrato_datos(contrato, vencimiento))
            if razon:
                atencion.append({**contrato_datos(contrato, vencimiento), 'razon_atencion': razon})
        proximos.sort(key=lambda contrato: contrato['vencimiento_operativo']['fecha'])
        tareas = []
        tareas_vencidas = 0
        if tiene(request.user, 'view_historialestadocontrato'):
            from django.db.models import F
            from .models import ActuacionContractual

            pendientes_tarea = ActuacionContractual.objects.filter(
                tipo=ActuacionContractual.Tipo.TAREA, contrato__abogado_responsable=request.user,
                eventos__isnull=True,
            )
            tareas_vencidas = pendientes_tarea.filter(vence__lt=hoy).count()
            tareas = list(pendientes_tarea.annotate(numero_contrato=F('contrato__numero_contrato'))
                          .order_by(F('vence').asc(nulls_last=True), 'id')
                          .values('id', 'contrato_id', 'numero_contrato', 'titulo', 'vence')[:8])
        return Response({
            'total': total, 'activos': activos, 'pendientes_liquidacion': pendientes,
            'vencidos': vencidos, 'proximos_vencimientos': proximos[:8], 'requieren_atencion': atencion[:8],
            'tareas_pendientes': tareas, 'tareas_vencidas': tareas_vencidas,
            'alertas': resumen_panel(alertas, revisiones, prioridades),
            'puede_registrar_alertas': ver_alertas and tiene(request.user, 'change_contrato'),
            'definiciones': {
                'total': 'Expedientes del abogado autenticado.',
                'activos': 'Expedientes con una etapa operativa abierta, excluidos Liquidado y Cierre.',
                'pendientes_liquidacion': 'Expedientes sin registro de Liquidado o Cierre. Indicador de seguimiento, sujeto a revisión jurídica de cada expediente.',
                'vencidos': 'Etapas abiertas con vencimiento operativo confirmado anterior a hoy.',
                'proximos_vencimientos': 'Hasta ocho vencimientos operativos confirmados entre hoy y los próximos 30 días.',
                'requieren_atencion': 'Hasta ocho expedientes sin seguimiento, con vencimiento confirmado cumplido o con calendario pendiente de revisión.',
            },
        })
