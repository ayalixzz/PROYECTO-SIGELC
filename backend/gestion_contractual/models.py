from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from django.core.validators import FileExtensionValidator, MinValueValidator
from decimal import Decimal
import uuid
from pathlib import Path

from .archivos_modelos import ArchivoPrivado  # noqa: F401


class EntidadPublica(models.Model):
    nombre = models.CharField(max_length=255)
    nit = models.CharField('NIT', max_length=30, unique=True)
    direccion = models.CharField('dirección', max_length=255, blank=True)
    correo_institucional = models.EmailField(blank=True)
    telefono = models.CharField('teléfono', max_length=30, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'entidad pública'
        verbose_name_plural = 'entidades públicas'
        constraints = [models.UniqueConstraint(fields=['activo'], condition=Q(activo=True), name='una_entidad_activa')]

    def __str__(self):
        return self.nombre


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='perfil_sigelc')
    nombre_cargo = models.CharField(max_length=150, blank=True)
    dependencia = models.CharField(max_length=150, blank=True)
    telefono = models.CharField('teléfono', max_length=30, blank=True)
    activo = models.BooleanField(default=True)
    abogado_operativo = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['abogado_operativo'], condition=Q(abogado_operativo=True), name='un_abogado_operativo')]

    def __str__(self):
        return str(self.usuario)


class EstadoContrato(models.Model):
    codigo = models.SlugField('código', max_length=60, unique=True)
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField('descripción', blank=True)
    orden = models.PositiveIntegerField(default=0)
    activo = models.BooleanField(default=True)
    plazo_dias_habiles = models.PositiveSmallIntegerField('plazo de referencia en días hábiles', null=True, blank=True)

    class Meta:
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


class TransicionEstado(models.Model):
    origen = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT, related_name='transiciones_salientes')
    destino = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT, related_name='transiciones_entrantes')
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'transición de estado'
        verbose_name_plural = 'transiciones de estado'
        constraints = [models.UniqueConstraint(fields=['origen', 'destino'], name='transicion_unica'), models.CheckConstraint(condition=~Q(origen=models.F('destino')), name='transicion_distinta')]

    def __str__(self):
        return f'{self.origen} → {self.destino}'


class RegistroInmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('No se permite modificar registros históricos directamente.')

    def delete(self):
        raise ValidationError('No se permite eliminar registros históricos.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('No se permite modificar registros históricos directamente.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('No se permite crear registros históricos en bloque.')


class Auditoria(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='auditorias_sigelc')
    accion = models.CharField('acción', max_length=80)
    entidad_afectada = models.CharField(max_length=120)
    identificador_registro = models.CharField(max_length=100)
    fecha_hora = models.DateTimeField(auto_now_add=True)
    datos_anteriores = models.JSONField(default=dict, blank=True)
    datos_nuevos = models.JSONField(default=dict, blank=True)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        verbose_name = 'auditoría'
        verbose_name_plural = 'auditorías'
        ordering = ['-fecha_hora']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Los registros de auditoría no se pueden modificar.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los registros de auditoría no se pueden eliminar.')

    def __str__(self):
        return f'{self.accion}: {self.entidad_afectada} #{self.identificador_registro}'


class ContratoQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        raise ValidationError('La creación de contratos en bloque no está habilitada.')

    def update(self, **kwargs):
        raise ValidationError('Los contratos requieren una edición individual con versión esperada.')

    def bulk_update(self, objs, fields, **kwargs):
        raise ValidationError('No se permiten ediciones masivas sin control de versión.')

    def delete(self):
        raise ValidationError('No se permite eliminar contratos.')


class Contrato(models.Model):
    version_registro = models.PositiveIntegerField(default=1, editable=False)
    entidad = models.ForeignKey(EntidadPublica, on_delete=models.PROTECT, related_name='contratos')
    numero_contrato = models.CharField('número de contrato', max_length=80)
    anio_contractual = models.PositiveSmallIntegerField('año contractual')
    objeto_contractual = models.TextField()
    tipo_contrato = models.CharField(max_length=120)
    modalidad_contratacion = models.CharField('modalidad de contratación', max_length=150)
    contratista_nombre = models.CharField(max_length=255)
    contratista_identificacion = models.CharField('identificación del contratista', max_length=80)
    valor_inicial = models.DecimalField(max_digits=20, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    valor_actual = models.DecimalField(max_digits=20, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    fecha_suscripcion = models.DateField('fecha de suscripción')
    fecha_asignacion = models.DateField('fecha real de asignación', null=True, blank=True)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_terminacion = models.DateField('fecha de terminación', null=True, blank=True)
    estado_actual = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT, related_name='contratos')
    abogado_responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='contratos_abogado')
    supervisor_responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='contratos_supervisor', null=True, blank=True, editable=False)
    supervisor_nombre = models.CharField('supervisor contractual', max_length=255, blank=True)
    observaciones = models.TextField(blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    objects = ContratoQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['entidad', 'anio_contractual', 'numero_contrato'], name='contrato_entidad_anio_numero_unico'), models.UniqueConstraint(fields=['anio_contractual', 'numero_contrato'], name='contrato_anio_numero_global_unico'), models.CheckConstraint(condition=Q(valor_inicial__gte=0) & Q(valor_actual__gte=0), name='contrato_valores_no_negativos'), models.CheckConstraint(condition=Q(anio_contractual__gte=1900) & Q(anio_contractual__lte=9999), name='contrato_anio_valido')]

    def clean(self):
        super().clean()
        if self.fecha_inicio and self.fecha_terminacion and self.fecha_terminacion < self.fecha_inicio:
            raise ValidationError({'fecha_terminacion': 'La terminación no puede ser anterior al inicio.'})
        if self._state.adding and self.estado_actual_id and (self.estado_actual.codigo in ('liquidado', 'cierre') or self.estado_actual.nombre.strip().casefold() in ('liquidado', 'cierre')):
            raise ValidationError({'estado_actual': 'La liquidación y el cierre requieren reglas específicas aún no habilitadas.'})

    @classmethod
    def from_db(cls, db, field_names, values):
        obj = super().from_db(db, field_names, values)
        if 'version_registro' in field_names:
            obj._version_leida = obj.version_registro
        return obj

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        fields = kwargs.get('fields', args[1] if len(args)>1 else None)
        if fields is None:
            self._version_leida = self.version_registro

    @transaction.atomic
    def save(self, *args, **kwargs):
        from .integridad import ConflictoEdicion, valores
        previo = type(self).objects.select_for_update().filter(pk=self.pk).first() if self.pk else None
        if previo:
            if getattr(self, '_version_esperada', getattr(self, '_version_leida', None)) != previo.version_registro:
                raise ConflictoEdicion()
            if previo.estado_actual_id != self.estado_actual_id:
                raise ValidationError('El estado solo puede cambiarse mediante aprobación.')
            if previo.fecha_asignacion != self.fecha_asignacion and self.instancias.exists():
                raise ValidationError('La asignación queda fija una vez iniciado el seguimiento.')
            campos = kwargs.get('update_fields')
            if campos and {'estado_actual', 'estado_actual_id'} & set(campos):
                raise ValidationError('El estado solo puede cambiarse mediante aprobación.')
            self._valores_anteriores = valores(previo)
            self.version_registro = previo.version_registro + 1
            if campos is None:
                kwargs['update_fields'] = [f.name for f in self._meta.concrete_fields if not f.primary_key and f.name != 'estado_actual']
            else:
                kwargs['update_fields'] = set(campos) | {'version_registro', 'fecha_actualizacion'}
        else:
            self._valores_anteriores = {}
            self.version_registro = 1
        self.full_clean()
        super().save(*args, **kwargs)
        self._version_leida = self.version_registro
        self.__dict__.pop('_version_esperada', None)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar contratos.')

    def __str__(self):
        return f'{self.numero_contrato} / {self.anio_contractual}'


class CalculoCompetencia(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = 'calculado_pendiente_validacion', 'Calculado — pendiente de validación'
        VERIFICADO = 'verificado_por_abogado', 'Verificado por el abogado'
        INFORMACION = 'requiere_informacion', 'Requiere información'
        REVISION = 'requiere_revision', 'Requiere revisión'

    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='calculos_competencia')
    version = models.PositiveIntegerField()
    anterior = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT)
    operacion = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)
    calculado_en = models.DateTimeField(default=timezone.now)
    estado = models.CharField(max_length=35, choices=Estado.choices)
    insumos = models.JSONField()
    resultado = models.JSONField()
    cambios = models.JSONField(default=list)
    fundamento_verificacion = models.TextField(blank=True)
    motivo_rectificacion = models.TextField(blank=True)
    verificado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='competencias_verificadas')
    verificado_en = models.DateTimeField(null=True, blank=True)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(fields=['contrato', 'version'], name='competencia_version_unica'),
            models.CheckConstraint(
                condition=Q(estado='verificado_por_abogado', verificado_por__isnull=False, verificado_en__isnull=False) & ~Q(fundamento_verificacion='')
                | (~Q(estado='verificado_por_abogado') & Q(verificado_por__isnull=True, verificado_en__isnull=True)),
                name='competencia_verificacion_consistente',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Los cálculos se conservan como versiones históricas.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Se deben conservar las versiones del cálculo.')


class HistorialEstadoContrato(models.Model):
    class Resultado(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente'
        APROBADO = 'aprobado', 'Aprobado'
        RECHAZADO = 'rechazado', 'Rechazado'

    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='historial_estados')
    estado_anterior = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT, related_name='salidas_historial')
    estado_nuevo = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT, related_name='entradas_historial')
    solicitado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cambios_solicitados')
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cambios_aprobados', null=True, blank=True)
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    fecha_inicio_plazo = models.DateField(null=True, blank=True)
    fecha_vencimiento_plazo = models.DateField(null=True, blank=True)
    motivo = models.TextField()
    observaciones = models.TextField(blank=True)
    resultado = models.CharField(max_length=12, choices=Resultado.choices, default=Resultado.PENDIENTE)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        verbose_name = 'historial de estado de contrato'
        verbose_name_plural = 'historial de estados de contrato'
        constraints = [models.CheckConstraint(condition=(Q(resultado='pendiente', aprobado_por__isnull=True, fecha_aprobacion__isnull=True) | Q(resultado__in=['aprobado', 'rechazado'], aprobado_por__isnull=False, fecha_aprobacion__isnull=False)), name='historial_decision_consistente')]

    def save(self, *args, **kwargs):
        if not getattr(self, '_por_servicio', False):
            raise ValidationError('El historial se registra mediante el servicio de estados.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar historial.')


def validar_tamano(archivo):
    if archivo.size > 20 * 1024 * 1024:
        raise ValidationError('El archivo no puede superar 20 MB.')


def ruta_documento(instance, filename):
    return f'contratos/{instance.contrato_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class TipoDocumental(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    registrado_en = models.DateTimeField(auto_now_add=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    def __str__(self):
        return self.nombre


class RequisitoDocumental(models.Model):
    tipo = models.ForeignKey(TipoDocumental, on_delete=models.PROTECT)
    clase_expediente = models.CharField(max_length=120, blank=True, help_text='Tipo de contrato. Vacío conserva el requisito sin aplicarlo.')
    activo = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.tipo} · {self.clase_expediente or "Aplicabilidad pendiente"}'

    class Meta:
        constraints = [models.UniqueConstraint(fields=['tipo', 'clase_expediente'], name='requisito_tipo_clase_unico')]


class EstadoRevision(models.TextChoices):
    PENDIENTE = 'pendiente', 'Pendiente de revisión'
    EN_REVISION = 'en_revision', 'En revisión'
    CONFORME = 'conforme', 'Conforme'
    OBSERVADO = 'observado', 'Con observaciones'
    NO_APLICA = 'no_aplica', 'No aplica'


class DocumentoContrato(models.Model):
    objects = RegistroInmutableQuerySet.as_manager()
    motivo_rectificacion = models.TextField(blank=True)
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='documentos')
    tipo_documento = models.CharField(max_length=120)
    nombre_original = models.CharField(max_length=255)
    archivo = models.FileField(upload_to=ruta_documento, validators=[FileExtensionValidator(['pdf', 'docx', 'xlsx']), validar_tamano], max_length=255)
    descripcion = models.TextField('descripción', blank=True)
    cargado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='documentos_cargados')
    fecha_carga = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True)
    categoria = models.ForeignKey(TipoDocumental, null=True, blank=True, on_delete=models.PROTECT)
    fecha_recepcion = models.DateField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    anterior = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, editable=False, related_name='documento_contrato_siguiente')
    estado_revision = models.CharField(max_length=20, choices=EstadoRevision.choices, default=EstadoRevision.PENDIENTE)
    operacion_recepcion = models.UUIDField(null=True, unique=True, editable=False)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            if not getattr(self, '_revision_autorizada', False) or set(kwargs.get('update_fields') or ()) != {'estado_revision'}:
                raise ValidationError('El documento histórico se conserva; registre una nueva versión con motivo.')
            original = type(self).objects.get(pk=self.pk)
            if any(getattr(original,f.attname) != getattr(self,f.attname) for f in self._meta.concrete_fields if f.name != 'estado_revision'):
                raise ValidationError('La revisión no permite sustituir el soporte histórico.')
            if hasattr(original, 'generacion'):
                raise ValidationError('Los borradores se corrigen mediante una nueva versión.')
        if self.anterior_id:
            if (self.anterior.contrato_id, self.anterior.version + 1) != (self.contrato_id, self.version):
                raise ValidationError('La rectificación conserva expediente y secuencia de versiones.')
            if self._state.adding and not self.motivo_rectificacion.strip():
                raise ValidationError('Indique el motivo de rectificación documental.')
        self.full_clean()
        if self._state.adding and self.archivo and not self.archivo._committed:
            from .archivos_transaccionales import escritura_con_archivo
            entrada = self.archivo.file
            entrada.seek(0)
            with escritura_con_archivo() as guardar:
                guardar(self.archivo, self.nombre_original, entrada.read())
                return self.save(*args, **kwargs)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se eliminan documentos históricos.')

    def __str__(self):
        return self.nombre_original


class TipoRequerimiento(models.TextChoices):
    PRIMERO = 'primer-requerimiento', 'Primer requerimiento'
    SEGUNDO = 'segundo-requerimiento', 'Segundo requerimiento'
    TERCERO = 'tercer-requerimiento', 'Tercer requerimiento'
    OBSERVACIONES = 'observaciones', 'Observaciones documentales'
    GCOF19 = 'gcof19', 'Informe final GCOF19'


class RegistroRevisionInteligente(models.Model):
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('El registro de revisión se conserva como historial.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('El registro de revisión se conserva como historial.')


class RevisionInteligente(RegistroRevisionInteligente):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='revisiones_inteligentes')
    anterior = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='siguiente')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    etapa = models.CharField(max_length=60)
    instancia = models.ForeignKey('InstanciaSeguimiento', on_delete=models.PROTECT, null=True)
    fecha = models.DateTimeField(auto_now_add=True)
    operacion = models.UUIDField(unique=True)
    huella = models.CharField(max_length=64)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['contrato', 'version'], name='revision_inteligente_version_unica')]


class FuenteRevisionInteligente(RegistroRevisionInteligente):
    revision = models.ForeignKey(RevisionInteligente, on_delete=models.PROTECT, related_name='fuentes_revision')
    archivo = models.FileField(upload_to='revision-inteligente/%Y/%m/', max_length=255)
    nombre = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    tipo = models.CharField(max_length=8)
    tamano = models.PositiveIntegerField()
    clasificacion = models.CharField(max_length=100)
    fragmentos = models.JSONField(default=list)
    advertencias = models.JSONField(default=list)
    procedencia = models.JSONField(default=dict)


class EventoRevisionInteligente(RegistroRevisionInteligente):
    revision = models.ForeignKey(RevisionInteligente, on_delete=models.PROTECT, related_name='eventos_revision')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=16)
    fecha = models.DateTimeField(auto_now_add=True)
    operacion = models.UUIDField(unique=True)
    huella = models.CharField(max_length=64)
    datos = models.JSONField()

    class Meta:
        ordering = ['pk']


class ObservacionDocumental(models.Model):
    class Hallazgo(models.TextChoices):
        FALTANTE = 'faltante', 'Soporte faltante'
        INCONSISTENCIA = 'inconsistencia', 'Inconsistencia observada'
        ACLARACION = 'aclaracion', 'Solicitud de aclaración'
        OTRO = 'otro', 'Otro hallazgo'

    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='observaciones_documentales')
    documentos = models.ManyToManyField(DocumentoContrato, through='SoporteObservacion', blank=True)
    descripcion = models.TextField()
    tipo = models.CharField(max_length=20, choices=Hallazgo.choices)
    estado_revision = models.CharField(max_length=20, choices=EstadoRevision.choices)
    evidencia = models.TextField(blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    registrada_en = models.DateTimeField(auto_now_add=True)
    anterior = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='rectificacion')
    motivo = models.TextField(blank=True)
    operacion = models.UUIDField(unique=True)
    objects = RegistroInmutableQuerySet.as_manager()

    def __str__(self):
        return f'#{self.pk} · {self.descripcion[:80]}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registre una rectificación para conservar la observación anterior.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar observaciones históricas.')


class SoporteObservacion(models.Model):
    observacion = models.ForeignKey(ObservacionDocumental, on_delete=models.PROTECT)
    documento = models.ForeignKey(DocumentoContrato, on_delete=models.PROTECT)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['observacion', 'documento'], name='observacion_soporte_unico')]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.observacion.contrato_id != self.documento.contrato_id:
            raise ValidationError('El soporte debe pertenecer al contrato de la observación.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Se conservan los soportes históricos.')


class ObligacionContractual(models.Model):
    class Tipo(models.TextChoices):
        GENERAL = 'general', 'General'
        ESPECIFICA = 'especifica', 'Específica'

    class Verificacion(models.TextChoices):
        PENDIENTE = 'pendiente', 'Pendiente de validación'
        VERIFICADA = 'verificada', 'Datos verificados documentalmente por el abogado'

    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='obligaciones')
    numero = models.PositiveIntegerField('número u orden')
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    descripcion = models.TextField('descripción contractual')
    actividad = models.TextField('actividad informada', blank=True)
    producto = models.TextField('producto informado', blank=True)
    comentario_supervisor = models.TextField('comentario aportado por el supervisor', blank=True)
    comentario_verificado = models.BooleanField('atribución al supervisor verificada con soporte', default=False)
    porcentaje = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    estado_verificacion = models.CharField(max_length=12, choices=Verificacion.choices, default=Verificacion.PENDIENTE)
    fundamento = models.TextField('referencia documental de verificación', blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    registrada_en = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)
    anterior = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='rectificacion')
    motivo = models.TextField(blank=True)
    operacion = models.UUIDField(unique=True)
    documentos = models.ManyToManyField(DocumentoContrato, through='SoporteObligacion', blank=True)
    observaciones_abogado = models.ManyToManyField(ObservacionDocumental, through='HallazgoObligacion', blank=True)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        ordering = ['tipo', 'numero', 'version']
        constraints = [
            models.UniqueConstraint(fields=['contrato', 'tipo', 'numero', 'version'], name='obligacion_version_unica'),
            models.CheckConstraint(condition=Q(numero__gte=1) & Q(version__gte=1), name='obligacion_orden_version_positivos'),
            models.CheckConstraint(condition=Q(porcentaje__isnull=True) | Q(porcentaje__gte=0, porcentaje__lte=100), name='obligacion_porcentaje_rango'),
            models.CheckConstraint(condition=Q(porcentaje__isnull=True) | Q(estado_verificacion='verificada') & ~Q(fundamento=''), name='obligacion_porcentaje_documentado'),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.numero} · v{self.version}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registre una rectificación de la obligación; se conserva la versión anterior.')
        if self.anterior_id and (self.anterior.contrato_id, self.anterior.tipo, self.anterior.numero, self.anterior.version + 1) != (self.contrato_id, self.tipo, self.numero, self.version):
            raise ValidationError('La rectificación debe conservar el contrato, tipo y número y avanzar una versión.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se eliminan obligaciones históricas.')


class SoporteObligacion(models.Model):
    obligacion = models.ForeignKey(ObligacionContractual, on_delete=models.PROTECT)
    documento = models.ForeignKey(DocumentoContrato, on_delete=models.PROTECT)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['obligacion', 'documento'], name='obligacion_soporte_unico')]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.obligacion.contrato_id != self.documento.contrato_id or hasattr(self.documento, 'generacion'):
            raise ValidationError('El soporte debe ser un documento fuente del mismo contrato; el vínculo histórico no se modifica.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Se conservan los soportes de la obligación.')


class HallazgoObligacion(models.Model):
    obligacion = models.ForeignKey(ObligacionContractual, on_delete=models.PROTECT)
    observacion = models.ForeignKey(ObservacionDocumental, on_delete=models.PROTECT)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['obligacion', 'observacion'], name='obligacion_hallazgo_unico')]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.obligacion.contrato_id != self.observacion.contrato_id:
            raise ValidationError('El hallazgo debe pertenecer al contrato; el vínculo histórico no se modifica.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Se conservan las referencias a hallazgos.')


def ruta_plantilla(instance, filename):
    return f'plantillas/{uuid.uuid4().hex}.docx'


class TipoActa(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    descripcion = models.TextField(blank=True)
    aplicabilidad = models.TextField(blank=True, help_text='Regla institucional aprobada. El abogado confirma la selección.')
    activo = models.BooleanField(default=True)

    def __str__(self):
        return self.nombre

    @property
    def clave_documental(self):
        return f'acta-{self.pk}'


class PlantillaDocumento(models.Model):
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=30)
    tipo_acta = models.ForeignKey(TipoActa, null=True, blank=True, on_delete=models.PROTECT, related_name='plantillas')
    ficticia = models.BooleanField(default=False)
    version = models.PositiveIntegerField()
    archivo = models.FileField(upload_to=ruta_plantilla)
    nombre_original = models.CharField(max_length=255)
    incorporada_en = models.DateTimeField(auto_now_add=True)
    incorporada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    activa = models.BooleanField(default=False)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['tipo', 'version'], name='plantilla_tipo_version_unica'),
            models.UniqueConstraint(fields=['tipo'], condition=Q(activa=True), name='plantilla_activa_por_tipo'),
        ]

    def __str__(self):
        return f'{self.nombre} (v{self.version})'

    def get_tipo_display(self):
        return self.tipo_acta.nombre if self.tipo_acta_id else TipoRequerimiento(self.tipo).label

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('La plantilla original se conserva. Registre una nueva versión.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar versiones de plantilla.')


class DocumentoGenerado(models.Model):
    documento = models.OneToOneField(DocumentoContrato, on_delete=models.PROTECT, related_name='generacion')
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='borradores_requerimiento')
    plantilla = models.ForeignKey(PlantillaDocumento, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=30)
    calculo_competencia = models.ForeignKey(CalculoCompetencia, null=True, blank=True, on_delete=models.PROTECT)
    motivo_revision = models.TextField(blank=True)

    def get_tipo_display(self):
        return self.plantilla.get_tipo_display()
    version = models.PositiveIntegerField()
    anterior = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT)
    datos_utilizados = models.JSONField(default=dict)
    campos_pendientes = models.JSONField(default=list, blank=True)
    estado_revision = models.CharField(max_length=20, default='borrador', editable=False)
    operacion = models.UUIDField(null=True, unique=True, editable=False)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['contrato', 'tipo', 'version'], name='borrador_version_unica')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Las revisiones se guardan como nuevas versiones.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar borradores históricos.')


class RegistroLiquidacionBase(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT)
    clave = models.CharField(max_length=100)
    documento = models.ForeignKey(DocumentoContrato, on_delete=models.PROTECT, null=True, blank=True)
    estado = models.CharField(max_length=12, choices=[('pendiente','Pendiente de verificación'),('verificado','Verificado documentalmente')], default='pendiente')
    fundamento = models.TextField(blank=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    registrado_en = models.DateTimeField(auto_now_add=True)
    validado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    validado_en = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    anterior = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='rectificacion')
    motivo = models.TextField(blank=True)
    operacion = models.UUIDField(unique=True)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        abstract = True
        constraints = [models.UniqueConstraint(fields=['contrato','clave','version'],name='%(class)s_version_unica'),
                      models.CheckConstraint(condition=Q(version__gte=1),name='%(class)s_version_positiva'),
                      models.CheckConstraint(condition=Q(estado='pendiente',validado_por__isnull=True,validado_en__isnull=True) | Q(estado='verificado',validado_por__isnull=False,validado_en__isnull=False) & ~Q(fundamento='') & Q(documento__isnull=False),name='%(class)s_verificacion')]

    def __str__(self):
        return f'{self.clave} · v{self.version} · {self.get_estado_display()}'

    @transaction.atomic
    def save(self,*args,**kwargs):
        if not self._state.adding:
            raise ValidationError('Registre una rectificación; no se modifica una versión histórica.')
        Contrato.objects.select_for_update().get(pk=self.contrato_id)
        if self.documento_id and (self.documento.contrato_id != self.contrato_id or hasattr(self.documento,'generacion')):
            raise ValidationError('La fuente debe ser un documento no generado del mismo contrato.')
        if self.anterior_id and (self.anterior.contrato_id,self.anterior.clave,self.anterior.version+1)!=(self.contrato_id,self.clave,self.version):
            raise ValidationError('La rectificación conserva contrato y clave y avanza una versión.')
        self.full_clean()
        super().save(*args,**kwargs)

    def delete(self,*args,**kwargs):
        raise ValidationError('No se eliminan registros históricos de liquidación.')


class GarantiaContractual(RegistroLiquidacionBase):
    seccion = models.CharField(max_length=10,choices=[('principal','Contrato principal'),('extension','Extensión de liquidación')])
    aseguradora = models.CharField(max_length=255,blank=True)
    poliza = models.CharField(max_length=100,blank=True)
    anexo = models.CharField(max_length=80,blank=True)
    fecha_expedicion = models.DateField(null=True,blank=True)
    fecha_aprobacion = models.DateField(null=True,blank=True)
    amparo = models.CharField(max_length=255,blank=True)
    valor_asegurado = models.DecimalField(max_digits=20,decimal_places=2,null=True,blank=True)
    vigencia_inicio = models.DateField(null=True,blank=True)
    vigencia_fin = models.DateField(null=True,blank=True)

    class Meta(RegistroLiquidacionBase.Meta):
        abstract = False
        constraints = RegistroLiquidacionBase.Meta.constraints + [
            models.CheckConstraint(condition=Q(valor_asegurado__isnull=True)|Q(valor_asegurado__gte=0),name='garantia_valor_no_negativo'),
            models.CheckConstraint(condition=Q(vigencia_inicio__isnull=True)|Q(vigencia_fin__isnull=True)|Q(vigencia_fin__gte=models.F('vigencia_inicio')),name='garantia_vigencia_orden')]


class OperacionPago(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT)
    identificador = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    referencia = models.CharField(max_length=150)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    registrado_en = models.DateTimeField(auto_now_add=True)
    objects = RegistroInmutableQuerySet.as_manager()

    def __str__(self):
        return f'{self.referencia} · {self.identificador}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('La identidad económica de un pago no se sobrescribe.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se elimina una operación económica.')


class PagoContractual(RegistroLiquidacionBase):
    operacion_economica = models.ForeignKey(OperacionPago, on_delete=models.PROTECT, null=True, blank=True)
    posible_duplicado = models.BooleanField(default=False, editable=False)
    naturaleza = models.CharField(max_length=25,choices=[('factura_emitida','Factura emitida'),('factura_aprobada','Factura aprobada'),('programado','Pago programado'),('realizado','Pago realizado acreditado'),('tesoreria','Pago certificado por Tesorería')])
    referencia = models.CharField(max_length=150)
    rubro = models.CharField(max_length=150,blank=True)
    orden = models.CharField(max_length=100,blank=True)
    fecha = models.DateField(null=True,blank=True)
    valor = models.DecimalField(max_digits=20,decimal_places=2,null=True,blank=True)
    certificador = models.CharField(max_length=255,blank=True)
    fecha_certificacion = models.DateField(null=True,blank=True)

    class Meta(RegistroLiquidacionBase.Meta):
        abstract = False
        constraints = RegistroLiquidacionBase.Meta.constraints + [models.CheckConstraint(condition=Q(valor__isnull=True)|Q(valor__gte=0),name='pago_valor_no_negativo')]


    def clean(self):
        super().clean()
        if self.operacion_economica_id and self.operacion_economica.contrato_id != self.contrato_id:
            raise ValidationError('La operación económica pertenece a otro expediente.')
        if self.estado == 'verificado' and self.naturaleza in ('realizado', 'tesoreria'):
            if not self.operacion_economica_id or self.posible_duplicado:
                raise ValidationError('Acredite una identidad económica sin ambigüedad antes de verificar el pago.')
            from .pagos_integridad import IDENTIDAD, CONSISTENCIA
            otros = type(self).objects.filter(contrato_id=self.contrato_id, rectificacion__isnull=True,
                naturaleza__in=('realizado', 'tesoreria')).exclude(pk=self.anterior_id)
            if otros.filter(**{k:getattr(self,k) for k in IDENTIDAD}).exclude(operacion_economica_id=self.operacion_economica_id).exists():
                raise ValidationError('Posible duplicado: revise la identidad económica del desembolso.')
            for otro in otros.filter(operacion_economica_id=self.operacion_economica_id, estado='verificado'):
                if any(getattr(self,k) != getattr(otro,k) for k in CONSISTENCIA):
                    raise ValidationError('Las evidencias de un desembolso difieren.')


class DatoLiquidacion(RegistroLiquidacionBase):
    clase = models.CharField(max_length=15,choices=[('representacion','Representación y facultades'),('contractual','Dato contractual adicional'),('balance','Campo del balance'),('declaracion','Declaración propuesta')])
    texto = models.TextField(blank=True)
    valor = models.DecimalField(max_digits=20,decimal_places=2,null=True,blank=True)
    emisor = models.CharField(max_length=255,blank=True)
    fecha_fuente = models.DateField(null=True,blank=True)

    class Meta(RegistroLiquidacionBase.Meta):
        abstract = False
        constraints = RegistroLiquidacionBase.Meta.constraints + [models.CheckConstraint(condition=Q(valor__isnull=True)|Q(valor__gte=0),name='balance_valor_no_negativo')]


class SoporteCambioEstado(models.Model):
    historial = models.ForeignKey(HistorialEstadoContrato, on_delete=models.PROTECT, related_name='soportes')
    documento = models.ForeignKey(DocumentoContrato, on_delete=models.PROTECT, related_name='cambios_estado')
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        verbose_name = 'soporte de cambio de estado'
        constraints = [models.UniqueConstraint(fields=['historial', 'documento'], name='soporte_cambio_unico')]

    def save(self, *args, **kwargs):
        if not getattr(self, '_por_servicio', False) or self.pk:
            raise ValidationError('Los soportes históricos solo pueden registrarse al cambiar el estado.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar soportes históricos.')


class CalendarioVerificado(models.Model):
    anio = models.PositiveSmallIntegerField()
    fuente = models.URLField(max_length=500)
    festivos = models.JSONField()
    registrado_en = models.DateTimeField(auto_now_add=True)
    # NULL/legacy preserves imported calendars without inventing an approval.
    version = models.PositiveIntegerField(null=True, blank=True)
    estado = models.CharField(max_length=20, default='legacy', choices=[('legacy', 'Requiere revisión'), ('propuesta', 'Pendiente de validación'), ('aprobado', 'Aprobado')])
    anterior = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='sucesoras')
    propuesta = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='aprobacion')
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    aprobado_en = models.DateTimeField(null=True, blank=True)
    justificacion = models.TextField(blank=True)
    politica = models.JSONField(default=dict, blank=True)
    excepciones = models.JSONField(default=list, blank=True)
    fundamentos = models.JSONField(default=list, blank=True)
    advertencias = models.JSONField(default=list, blank=True)
    diferencias = models.JSONField(default=dict, blank=True)
    operacion = models.UUIDField(unique=True, null=True, blank=True)
    objects = RegistroInmutableQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding or (self.estado != 'legacy' and not getattr(self, '_por_servicio', False)):
            raise ValidationError('Las versiones de calendario se conservan sin modificaciones.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar versiones del calendario.')

    class Meta:
        verbose_name = 'calendario y vigencia jurídica'
        verbose_name_plural = 'Calendarios y vigencia jurídica'
        ordering = ['-anio', '-version', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['anio', 'version'], name='calendario_version_unica'),
            models.CheckConstraint(condition=Q(estado='legacy') | Q(version__gt=0, responsable__isnull=False, operacion__isnull=False), name='calendario_autoria_version'),
            models.CheckConstraint(condition=~Q(estado='aprobado') | Q(aprobado_en__isnull=False, propuesta__isnull=False), name='calendario_aprobacion_completa'),
        ]


class EvaluacionCalendario(models.Model):
    """Append-only impact and explicit open-deadline decisions; never rewrites history."""
    calendario = models.ForeignKey(CalendarioVerificado, on_delete=models.PROTECT, related_name='evaluaciones')
    instancia = models.ForeignKey('InstanciaSeguimiento', on_delete=models.PROTECT, null=True, blank=True, related_name='evaluaciones_calendario')
    calculo = models.ForeignKey('CalculoCompetencia', on_delete=models.PROTECT, null=True, blank=True, related_name='evaluaciones_calendario')
    accion = models.CharField(max_length=20, choices=[('evaluar', 'Evaluación'), ('confirmar', 'Confirmación')])
    clasificacion = models.CharField(max_length=20, choices=[('sin_impacto','Sin impacto'), ('afectado','Afectado'), ('incierto','Requiere revisión')])
    detalle = models.JSONField()
    anterior = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='confirmacion')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    motivo = models.TextField()
    operacion = models.UUIDField(unique=True)
    registrado_en = models.DateTimeField(auto_now_add=True)
    objects = RegistroInmutableQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding or not getattr(self, '_por_servicio', False):
            raise ValidationError('Las evaluaciones y decisiones se conservan inmutables mediante el servicio.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar evaluaciones del calendario.')

    class Meta:
        ordering = ['-pk']
        constraints = [
            models.CheckConstraint(condition=Q(instancia__isnull=False, calculo__isnull=True) | Q(instancia__isnull=True, calculo__isnull=False), name='evaluacion_un_objetivo'),
            models.UniqueConstraint(fields=['calendario', 'instancia'], condition=Q(accion='evaluar'), name='impacto_instancia_unico'),
            models.UniqueConstraint(fields=['calendario', 'calculo'], condition=Q(accion='evaluar'), name='impacto_calculo_unico'),
        ]


class InstanciaSeguimiento(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='instancias')
    estado = models.ForeignKey(EstadoContrato, on_delete=models.PROTECT)
    orden = models.PositiveIntegerField()
    fecha_inicio = models.DateField()
    fecha_vencimiento = models.DateField(null=True, blank=True)
    plazo_dias_habiles = models.PositiveSmallIntegerField(null=True, blank=True)
    fecha_finalizacion = models.DateField(null=True, blank=True)
    evento_origen = models.OneToOneField(HistorialEstadoContrato, on_delete=models.PROTECT, null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    registrada_en = models.DateTimeField(auto_now_add=True)
    procedencia_calendario = models.JSONField(default=dict, blank=True)
    objects = RegistroInmutableQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding or not getattr(self, '_por_servicio', False):
            raise ValidationError('Las instancias solo se registran mediante el servicio de seguimiento.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('No se permite eliminar instancias de seguimiento.')

    class Meta:
        ordering = ['orden']
        constraints = [
            models.UniqueConstraint(fields=['contrato', 'orden'], name='instancia_orden_unico'),
            models.UniqueConstraint(fields=['contrato'], condition=Q(fecha_finalizacion__isnull=True), name='instancia_abierta_unica'),
            models.CheckConstraint(condition=Q(orden__gt=0), name='instancia_orden_positivo'),
            models.CheckConstraint(condition=Q(fecha_finalizacion__isnull=True) | Q(fecha_finalizacion__gte=models.F('fecha_inicio')), name='instancia_fin_valido'),
            models.CheckConstraint(
                condition=Q(plazo_dias_habiles__isnull=True, fecha_vencimiento__isnull=True)
                | Q(plazo_dias_habiles__gt=0, plazo_dias_habiles__isnull=False, fecha_vencimiento__isnull=False, fecha_vencimiento__gt=models.F('fecha_inicio')),
                name='instancia_plazo_consistente',
            ),
        ]


class RegistroAlertaContrato(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='alertas_registradas')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    clave = models.CharField(max_length=64)
    registrada_en = models.DateTimeField(auto_now_add=True)
    instantanea = models.JSONField()
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        ordering = ['-registrada_en', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['contrato', 'clave'], name='alerta_contrato_hito_unico'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError('El historial de alertas no puede modificarse.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('El historial de alertas no puede eliminarse.')


class PrioridadTemporalContrato(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name='prioridades_temporales')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operacion = models.UUIDField(unique=True)
    motivo = models.CharField(max_length=1000)
    vence = models.DateField()
    registrada_en = models.DateTimeField(auto_now_add=True)
    objects = RegistroInmutableQuerySet.as_manager()

    class Meta:
        ordering = ['-pk']

    def save(self, *args, **kwargs):
        if not self._state.adding or self.pk:
            raise ValidationError('El historial de prioridades no puede modificarse.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('El historial de prioridades no puede eliminarse.')


# These records are kept in a separate module but registered with this app.
from .actuaciones_models import ActuacionContractual, EventoActuacionContractual  # noqa: E402,F401
