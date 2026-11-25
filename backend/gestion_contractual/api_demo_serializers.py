from collections.abc import Mapping
from decimal import Decimal

from rest_framework import serializers

from .models import Contrato


CAMPOS_EDITABLES = (
    'numero_contrato', 'anio_contractual', 'objeto_contractual', 'tipo_contrato',
    'modalidad_contratacion', 'contratista_nombre', 'contratista_identificacion',
    'valor_inicial', 'valor_adicional', 'valor_actual', 'fecha_suscripcion', 'fecha_asignacion',
    'fecha_inicio', 'fecha_terminacion', 'supervisor_nombre', 'supervisor_cargo', 'observaciones',
)


class EntradaEstricta:
    """Rechaza claves ajenas al formulario, incluidas relaciones protegidas."""

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            extras = set(data) - set(self.fields)
            if extras:
                raise serializers.ValidationError({campo: ['Campo no permitido.'] for campo in sorted(extras)})
        return super().to_internal_value(data)


class SesionEntrada(EntradaEstricta, serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=1024, trim_whitespace=False, write_only=True)


class ContratoEntrada(EntradaEstricta, serializers.ModelSerializer):
    anio_contractual = serializers.IntegerField(min_value=1900, max_value=9999)
    valor_inicial = serializers.DecimalField(max_digits=20, decimal_places=2, min_value=Decimal('0'))
    valor_adicional = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('0'), required=False,
    )
    valor_actual = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('0'), required=False,
    )

    class Meta:
        model = Contrato
        fields = CAMPOS_EDITABLES
        # El modelo valida unicidad sobre el expediente completo y la base protege carreras.
        validators = []

    def validate(self, attrs):
        if self.instance is None and 'valor_actual' not in attrs:
            attrs.setdefault('valor_adicional', Decimal('0.00'))
        adicional = attrs.get('valor_adicional', getattr(self.instance, 'valor_adicional', None))
        if adicional is not None and 'valor_actual' in attrs:
            inicial = attrs.get('valor_inicial', getattr(self.instance, 'valor_inicial', None))
            if attrs['valor_actual'] != inicial + adicional:
                raise serializers.ValidationError({
                    'valor_actual': ['El total debe coincidir con la suma del valor inicial y adicional.'],
                })
        return attrs


class ContratoEdicionEntrada(ContratoEntrada):
    version_registro = serializers.IntegerField(min_value=1, max_value=2147483647)

    class Meta(ContratoEntrada.Meta):
        fields = (*CAMPOS_EDITABLES, 'version_registro')

    def validate(self, attrs):
        if 'version_registro' not in attrs:
            raise serializers.ValidationError({'version_registro': ['Incluya la versión que abrió para editar.']})
        if len(attrs) == 1:
            raise serializers.ValidationError('Ingrese por lo menos un campo contractual.')
        return super().validate(attrs)


class ContratoSalida(serializers.ModelSerializer):
    class Meta:
        model = Contrato
        fields = ('id', *CAMPOS_EDITABLES, 'version_registro', 'fecha_creacion', 'fecha_actualizacion')


class FiltrosContrato(serializers.Serializer):
    buscar = serializers.CharField(max_length=150, required=False, allow_blank=True)
    anio = serializers.IntegerField(min_value=1900, max_value=9999, required=False)
    estado = serializers.SlugField(max_length=60, required=False)


class GeneracionEntrada(EntradaEstricta, serializers.Serializer):
    operacion = serializers.UUIDField()


class InicioSeguimientoEntrada(EntradaEstricta, serializers.Serializer):
    confirmado = serializers.BooleanField()
