"""Expone el cálculo jurídico versionado del expediente del abogado."""

from datetime import date

from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response

from .api_demo_serializers import EntradaEstricta
from .api_demo_views import DemoAPIView, expediente, exigir_permiso, tiene
from .calendarios_servicios import bloquear_calendarios, evaluar_vigencia
from .competencia import (
    Estado, FECHAS_RESULTADO, FUENTES, calculo_verificado_vigente, datos_contrato, registrar_calculo,
)
from .models import CalendarioVerificado


def _opcional(choices):
    return serializers.ChoiceField(choices=choices, allow_blank=True, required=False)


class InsumosCompetencia(EntradaEstricta, serializers.Serializer):
    fecha_terminacion = serializers.DateField(allow_null=True, required=False)
    sujeto = _opcional(('si', 'no'))
    regimen = _opcional(('art11_cpaca', 'otro'))
    origen = _opcional(('ejecucion', 'efectiva', 'acto', 'acuerdo', 'otra'))
    fecha_efectiva = serializers.DateField(allow_null=True, required=False)
    fecha_referencia = serializers.DateField(allow_null=True, required=False)
    soporte_terminacion = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    pacto = _opcional(('si', 'no'))
    cantidad = serializers.IntegerField(min_value=1, max_value=10000, allow_null=True, required=False)
    unidad = _opcional(('dias_calendario', 'meses', 'anios', 'dias_habiles'))
    referencia_pacto = serializers.DateField(allow_null=True, required=False)
    liquidacion = _opcional(('ninguna', 'bilateral', 'unilateral'))
    fecha_liquidacion = serializers.DateField(allow_null=True, required=False)
    firma_acta = serializers.DateField(allow_null=True, required=False)
    ejecutoria_acto = serializers.DateField(allow_null=True, required=False)
    incidencias = _opcional(('si', 'no'))
    referencias = serializers.CharField(max_length=4000, allow_blank=True, required=False)
    observaciones = serializers.CharField(max_length=4000, allow_blank=True, required=False)


class FechasRectificadas(EntradaEstricta, serializers.Serializer):
    fecha_base = serializers.DateField(allow_null=True)
    fin_bilateral = serializers.DateField(allow_null=True)
    fin_unilateral = serializers.DateField(allow_null=True)
    inicio_judicial = serializers.DateField(allow_null=True)
    fin_judicial = serializers.DateField(allow_null=True)
    limite_competencia = serializers.DateField(allow_null=True)

    def validate(self, attrs):
        for campo, fecha in attrs.items():
            if fecha and fecha.year < 1984 and campo not in ('fecha_base', 'inicio_judicial'):
                raise serializers.ValidationError({campo: 'No hay cobertura de calendario para ese año.'})
        return attrs


class EntradaCompetencia(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()
    anterior_id = serializers.IntegerField(min_value=1, allow_null=True)
    accion = serializers.ChoiceField(choices=('calcular', 'verificar', 'rectificar'))
    datos = InsumosCompetencia(required=False)
    rectificacion = FechasRectificadas(required=False)
    fundamento = serializers.CharField(max_length=4000, allow_blank=True, required=False)
    motivo = serializers.CharField(max_length=1000, allow_blank=True, required=False)
    confirmar = serializers.BooleanField(required=False)

    def validate(self, attrs):
        accion = attrs['accion']
        if accion == 'calcular':
            if 'datos' not in attrs or 'rectificacion' in attrs or attrs.get('confirmar') or attrs.get('fundamento'):
                raise serializers.ValidationError('Envíe solo los insumos de la propuesta; la verificación es posterior.')
        else:
            if ('datos' in attrs or attrs['anterior_id'] is None or not attrs.get('fundamento')
                    or self.initial_data.get('confirmar') is not True):
                raise serializers.ValidationError('Confirme la revisión de una versión vigente e indique el fundamento.')
            if accion == 'rectificar' and ('rectificacion' not in attrs or not attrs.get('motivo')):
                raise serializers.ValidationError('Indique las fechas rectificadas y el motivo del ajuste.')
            if accion == 'verificar' and ('rectificacion' in attrs or attrs.get('motivo')):
                raise serializers.ValidationError('Verifique la propuesta sin alterar sus fechas.')
        return attrs


def _serializar_fechas(datos):
    return {campo: valor.isoformat() if isinstance(valor, date) else valor for campo, valor in datos.items()}


def _base_version(registro):
    return {
        'id': registro.pk, 'version': registro.version,
        'estado': registro.estado, 'estado_label': registro.get_estado_display(),
        'calculado_en': registro.calculado_en, 'verificado_en': registro.verificado_en,
        'verificado_por': registro.verificado_por.get_username() if registro.verificado_por else None,
    }


def _ficha(contrato, usuario):
    versiones = list(contrato.calculos_competencia.select_related('verificado_por')[:25])
    actual = versiones[0] if versiones else None
    editable = tiene(usuario, 'change_contrato')
    definitivos = datos_contrato(contrato)
    anios_aprobados = set(CalendarioVerificado.objects.filter(estado='aprobado').values_list('anio', flat=True))
    anios_pendientes = set()
    verificacion = None
    ficha = None
    if actual:
        sin_cambio = all(actual.insumos.get(k) == v for k, v in definitivos.items())
        vigencia = evaluar_vigencia(actual)
        cobertura = vigencia['clasificacion'] == 'sin_impacto'
        if not sin_cambio:
            verificacion = 'Los datos contractuales cambiaron; calcule una nueva propuesta.'
        elif not cobertura:
            verificacion = vigencia['motivo']
        confirmado = calculo_verificado_vigente(contrato) if actual.estado == Estado.VERIFICADO else None
        fechas = {campo: actual.resultado.get(campo) for campo in FECHAS_RESULTADO}
        consultas = actual.resultado.get('dependencias_calendario', {}).get('fechas', [])
        anios_pendientes = {d['anio'] for d in consultas if type(d.get('anio')) is int} - anios_aprobados
        ficha = {
            **_base_version(actual),
            'fechas': fechas,
            'reglas': actual.resultado.get('reglas', []),
            'advertencias': list(dict.fromkeys(
                actual.resultado.get('advertencias', []) + actual.resultado.get('advertencias_calendario', []),
            )),
            'faltantes': actual.resultado.get('faltantes', []),
            'fuentes': actual.resultado.get('fuentes', FUENTES),
            'version_reglas': actual.resultado.get('version_reglas'),
            'fundamento': actual.fundamento_verificacion,
            'motivo': actual.motivo_rectificacion,
            'vigente': confirmado is not None and confirmado.pk == actual.pk,
            'puede_verificar': editable and actual.estado == Estado.PENDIENTE and sin_cambio and cobertura,
            'puede_rectificar': editable and sin_cambio,
        }
    soportes = []
    if tiene(usuario, 'view_documentocontrato'):
        soportes = list(contrato.documentos.filter(activo=True).order_by('-pk').values('id', 'nombre_original'))
    return {
        'actual': ficha,
        'versiones': [_base_version(registro) for registro in versiones],
        'insumos': {**(actual.insumos if actual else {}), **definitivos},
        'puede_editar': editable,
        'fecha_confirmada': ficha['fechas']['limite_competencia'] if ficha and ficha['vigente'] else None,
        'motivo_revision': verificacion,
        'anios_aprobados': sorted(anios_aprobados),
        'anios_pendientes': sorted(anios_pendientes),
        'fuentes': FUENTES,
        'soportes': soportes,
    }


class CompetenciaView(DemoAPIView):
    @transaction.atomic
    def get(self, request, pk):
        bloquear_calendarios()
        return Response(_ficha(expediente(request.user, pk), request.user))

    @transaction.atomic
    def post(self, request, pk):
        contrato = expediente(request.user, pk)
        exigir_permiso(tiene(request.user, 'change_contrato'))
        entrada = EntradaCompetencia(data=request.data)
        entrada.is_valid(raise_exception=True)
        datos = dict(entrada.validated_data)
        datos.pop('confirmar', None)
        for campo in ('datos', 'rectificacion'):
            if campo in datos:
                datos[campo] = _serializar_fechas(datos[campo])
        soporte = datos.get('datos', {}).get('soporte_terminacion')
        if soporte:
            exigir_permiso(tiene(request.user, 'view_documentocontrato'))
            if not contrato.documentos.filter(pk=soporte, activo=True).exists():
                raise serializers.ValidationError({'soporte_terminacion': 'Seleccione un documento disponible del expediente.'})
        registrar_calculo(contrato_id=contrato.pk, usuario=request.user, **datos)
        return Response(_ficha(contrato, request.user), status=status.HTTP_201_CREATED)
