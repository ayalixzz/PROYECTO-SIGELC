from django.contrib import admin, messages
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils import timezone
from .access import es_abogado_operativo
from . import admin_competencia, admin_requerimientos, admin_revision, admin_gcof19, admin_liquidacion, admin_calendarios
from .models import RequisitoDocumental, TipoActa, TipoDocumental

from .models import Auditoria, Contrato, DocumentoContrato, EntidadPublica, EstadoContrato, HistorialEstadoContrato, PerfilUsuario, TransicionEstado


class AdminOperativo(admin.ModelAdmin):
    def has_module_permission(self, request):
        return es_abogado_operativo(request.user) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return es_abogado_operativo(request.user) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return es_abogado_operativo(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return es_abogado_operativo(request.user) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from django.db import transaction
        with transaction.atomic():
            super().save_model(request, obj, form, change)
            Auditoria.objects.create(usuario=request.user, accion='editar' if change else 'crear', entidad_afectada=obj._meta.label, identificador_registro=str(obj.pk), datos_nuevos={'campos_modificados': list(form.changed_data)})


class AdminCatalogoDocumental(admin.ModelAdmin):
    def has_view_permission(self, request, obj=None):
        return es_abogado_operativo(request.user) and request.user.has_perm('gestion_contractual.view_documentocontrato')

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_add_permission(self, request):
        return self.has_change_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request) and request.user.has_perm('gestion_contractual.change_documentocontrato')

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if isinstance(obj, TipoDocumental) and not change:
            obj.responsable = request.user
        super().save_model(request, obj, form, change)
        Auditoria.objects.create(usuario=request.user, accion='configurar_catalogo_documental',
                                 entidad_afectada=type(obj).__name__, identificador_registro=str(obj.pk),
                                 datos_nuevos={'campos_modificados': form.changed_data})


@admin.register(TipoDocumental)
class TipoDocumentalAdmin(AdminCatalogoDocumental):
    list_display = ('nombre', 'activo', 'responsable', 'registrado_en')
    readonly_fields = ('responsable', 'registrado_en')
    search_fields = ('nombre',)


@admin.register(RequisitoDocumental)
class RequisitoDocumentalAdmin(AdminCatalogoDocumental):
    list_display = ('tipo', 'clase_expediente', 'activo')


@admin.register(TipoActa)
class TipoActaAdmin(AdminCatalogoDocumental):
    list_display = ('nombre', 'activo')
    search_fields = ('nombre',)


@admin.register(EntidadPublica)
class EntidadPublicaAdmin(AdminOperativo):
    list_display = ('nombre', 'nit', 'activo', 'fecha_actualizacion')
    search_fields = ('nombre', 'nit')
    list_filter = ('activo',)
    readonly_fields = ('fecha_creacion', 'fecha_actualizacion')

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not EntidadPublica.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(AdminOperativo):
    list_display = ('usuario', 'nombre_cargo', 'dependencia', 'activo')
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name', 'dependencia')
    list_filter = ('activo', 'dependencia')
    autocomplete_fields = ('usuario',)
    readonly_fields = ('fecha_creacion',)


@admin.register(EstadoContrato)
class EstadoContratoAdmin(AdminOperativo):
    list_display = ('codigo', 'nombre', 'orden', 'plazo_dias_habiles', 'activo')
    search_fields = ('codigo', 'nombre')
    list_filter = ('activo',)


@admin.register(TransicionEstado)
class TransicionEstadoAdmin(AdminOperativo):
    list_display = ('origen', 'destino', 'activa')
    search_fields = ('origen__nombre', 'destino__nombre')
    list_filter = ('activa',)
    autocomplete_fields = ('origen', 'destino')


@admin.register(Auditoria)
class AuditoriaAdmin(AdminOperativo):
    list_display = ('fecha_hora', 'usuario', 'accion', 'entidad_afectada', 'identificador_registro')
    search_fields = ('accion', 'entidad_afectada', 'identificador_registro', 'usuario__username')
    list_filter = ('accion', 'entidad_afectada')
    readonly_fields = ('usuario', 'accion', 'entidad_afectada', 'identificador_registro', 'fecha_hora', 'datos_anteriores', 'datos_nuevos')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ContratoEdicionForm(forms.ModelForm):
    version_esperada = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Contrato
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['version_esperada'].initial = self.instance.version_registro

    def clean(self):
        datos = super().clean()
        if self.instance.pk:
            from .integridad import ConflictoEdicion
            if datos.get('version_esperada') != self.instance.version_registro:
                raise ConflictoEdicion()
            self.instance._version_esperada = datos['version_esperada']
        return datos


@admin.register(Contrato)
class ContratoAdmin(AdminOperativo):
    form = ContratoEdicionForm

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        from .integridad import ConflictoEdicion
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ConflictoEdicion:
            # La transacción de Admin ya se revirtió. El formulario vuelve a
            # validar el token antiguo frente a la versión confirmada.
            return super().changeform_view(request, object_id, form_url, extra_context)

    list_display = ('numero_contrato', 'anio_contractual', 'entidad', 'estado_actual', 'abogado_responsable', 'supervisor_nombre')
    search_fields = ('numero_contrato', 'contratista_nombre', 'contratista_identificacion')
    list_filter = ('anio_contractual', 'estado_actual', 'entidad')
    autocomplete_fields = ('entidad', 'estado_actual')
    readonly_fields = ('fecha_creacion', 'fecha_actualizacion', 'abogado_responsable', 'transicion_segura', 'etapas_registradas', 'borradores', 'acta_observaciones', 'informe_gcof19', 'acta_liquidacion', 'revision_documental', 'calculo_juridico')

    @admin.display(description='GENERAR ACTA DE LIQUIDACIÓN')
    def acta_liquidacion(self,obj):
        if obj.pk:
            return format_html('<a href="{}">Preparar garantías, pagos, balance y declaraciones</a>',reverse('admin:gestion_contractual_liquidacion',args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='GENERAR INFORME FINAL GCOF19')
    def informe_gcof19(self, obj):
        if obj.pk:
            return format_html('<a href="{}">Registrar obligaciones y preparar el informe GCOF19</a>', reverse('admin:gestion_contractual_obligaciones', args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='GENERAR ACTA DE OBSERVACIONES')
    def acta_observaciones(self, obj):
        if obj.pk:
            return format_html('<a href="{}?tipo=observaciones">GENERAR ACTA DE OBSERVACIONES</a>', reverse('admin:gestion_contractual_requerimientos', args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='Cálculo jurídico')
    def calculo_juridico(self, obj):
        if obj.pk:
            return format_html('<a href="{}">Consultar y verificar fechas jurídicas</a>', reverse('admin:gestion_contractual_competencia', args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='Revisión documental')
    def revision_documental(self, obj):
        if obj.pk:
            return format_html('<a href="{}">Revisar expediente documental</a>', reverse('admin:gestion_contractual_revision_documental', args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='GENERACIÓN DE DOCUMENTOS')
    def borradores(self, obj):
        if obj.pk:
            return format_html('<a href="{}">Generar, revisar y descargar requerimientos, observaciones y actas</a>', reverse('admin:gestion_contractual_requerimientos', args=[obj.pk]))
        return 'Disponible al guardar el contrato.'

    @admin.display(description='Instancias de seguimiento')
    def etapas_registradas(self, obj):
        if not obj.pk:
            return 'Borrador'
        from .calendarios_servicios import revisar_instancia
        def datos():
            for etapa in obj.instancias.select_related('estado'):
                revision = revisar_instancia(etapa)
                yield (etapa.orden,etapa.estado,etapa.fecha_inicio,etapa.fecha_vencimiento or 'Sin plazo',
                       revision['vigente'] or 'Sin plazo',revision['clasificacion'],
                       'PROVISIONAL: requiere calendario aprobado' if revision.get('provisional') else revision['motivo'],
                       etapa.fecha_finalizacion or 'Abierta')
        filas = format_html_join('', '<li>{}. {} · Inicio: {} · Vencimiento original: {} · Último confirmado: {} · Revisión: {} · {} · Finalización: {}</li>', datos())
        return format_html('<ol>{}</ol><p><a href="{}">Calendarios y vigencia jurídica</a></p>',filas,
                           reverse('admin:gestion_contractual_calendarioverificado_changelist')) if filas else 'Borrador'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'entidad' and request.resolver_match and request.resolver_match.url_name.endswith('_add'):
            kwargs['queryset'] = EntidadPublica.objects.filter(activo=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_urls(self):
        return [
            path('<int:contrato_id>/liquidacion/',self.admin_site.admin_view(lambda request,contrato_id:admin_liquidacion.expediente(request,self.admin_site,contrato_id)),name='gestion_contractual_liquidacion'),
            path('<int:contrato_id>/obligaciones/', self.admin_site.admin_view(lambda request, contrato_id: admin_gcof19.obligaciones(request, self.admin_site, contrato_id)), name='gestion_contractual_obligaciones'),
            path('<int:contrato_id>/competencia/', self.admin_site.admin_view(lambda request, contrato_id: admin_competencia.expediente(request, self.admin_site, contrato_id)), name='gestion_contractual_competencia'),
            path('<int:contrato_id>/revision-documental/', self.admin_site.admin_view(lambda request, contrato_id: admin_revision.expediente(request, self.admin_site, contrato_id)), name='gestion_contractual_revision_documental'),
            path('plantillas-requerimiento/', self.admin_site.admin_view(lambda request: admin_requerimientos.plantillas(request, self.admin_site)), name='gestion_contractual_plantillas_requerimiento'),
            path('<int:contrato_id>/requerimientos/', self.admin_site.admin_view(lambda request, contrato_id: admin_requerimientos.requerimientos(request, self.admin_site, contrato_id)), name='gestion_contractual_requerimientos'),
            path('<path:object_id>/iniciar-seguimiento/', self.admin_site.admin_view(self.iniciar_seguimiento_view), name='gestion_contractual_contrato_iniciar_seguimiento'),
            path('<path:object_id>/cambiar-estado/', self.admin_site.admin_view(self.cambiar_estado_view), name='gestion_contractual_contrato_cambiar_estado'),
        ] + super().get_urls()

    @admin.display(description='Estado')
    def transicion_segura(self, obj):
        if obj.pk:
            activa = obj.instancias.filter(fecha_finalizacion__isnull=True).exists()
            accion = 'cambiar_estado' if activa else 'iniciar_seguimiento'
            etiqueta = 'Cambiar estado' if activa else 'Iniciar seguimiento del borrador'
            return format_html('<a href="{}">{}</a>', reverse(f'admin:gestion_contractual_contrato_{accion}', args=[obj.pk]), etiqueta)
        return 'Disponible al guardar el contrato.'

    def iniciar_seguimiento_view(self, request, object_id):
        from .services import iniciar_seguimiento

        contrato = get_object_or_404(Contrato, pk=object_id)
        if not self.has_change_permission(request, contrato):
            raise PermissionDenied

        class FormularioInicio(forms.Form):
            confirmar = forms.BooleanField(label='Confirmo que Primer requerimiento empezó en la fecha de asignación y que no hay actuaciones previas')

        form = FormularioInicio(request.POST if request.method == 'POST' else None)
        if form.is_valid():
            try:
                iniciar_seguimiento(contrato_id=contrato.pk, usuario=request.user, sin_actuaciones_previas=form.cleaned_data['confirmar'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Seguimiento iniciado.')
                return redirect('admin:gestion_contractual_contrato_change', contrato.pk)
        return TemplateResponse(request, 'admin/gestion_contractual/cambiar_estado.html', {
            **self.admin_site.each_context(request), 'title': 'Iniciar seguimiento', 'form': form,
            'contrato': contrato, 'opts': self.model._meta, 'iniciando': True,
        })

    def cambiar_estado_view(self, request, object_id):
        from .services import cambiar_estado
        contrato = get_object_or_404(Contrato, pk=object_id)
        if not self.has_change_permission(request, contrato):
            raise PermissionDenied
        instancia = contrato.instancias.filter(fecha_finalizacion__isnull=True).first()
        if instancia is None:
            return redirect('admin:gestion_contractual_contrato_iniciar_seguimiento', contrato.pk)

        class FormularioCambio(forms.Form):
            estado = forms.ModelChoiceField(queryset=EstadoContrato.objects.filter(activo=True, transiciones_entrantes__origen=contrato.estado_actual, transiciones_entrantes__activa=True))
            instancia_esperada = forms.IntegerField(widget=forms.HiddenInput)
            fecha_efectiva = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
            motivo = forms.CharField(widget=forms.Textarea)
            observaciones = forms.CharField(widget=forms.Textarea, required=False)
            documentos = forms.ModelMultipleChoiceField(queryset=DocumentoContrato.objects.filter(contrato=contrato, activo=True), required=False, label='Soportes existentes')
            confirmado = forms.BooleanField(label='Confirmo este cambio de estado')
        form = FormularioCambio(request.POST if request.method == 'POST' else None,
                                initial={'instancia_esperada': instancia.pk, 'fecha_efectiva': timezone.localdate()})
        if request.method == 'POST' and form.is_valid():
            try:
                cambiar_estado(contrato_id=contrato.pk, estado_nuevo_id=form.cleaned_data['estado'].pk, usuario=request.user, motivo=form.cleaned_data['motivo'], confirmado=form.cleaned_data['confirmado'], instancia_esperada=form.cleaned_data['instancia_esperada'], fecha_efectiva=form.cleaned_data['fecha_efectiva'], observaciones=form.cleaned_data['observaciones'], documentos_ids=list(form.cleaned_data['documentos'].values_list('id', flat=True)))
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, 'Estado actualizado y registrado en el historial.')
                return redirect('admin:gestion_contractual_contrato_change', contrato.pk)
        return TemplateResponse(request, 'admin/gestion_contractual/cambiar_estado.html', {**self.admin_site.each_context(request), 'title': 'Cambiar estado del contrato', 'form': form, 'contrato': contrato, 'opts': self.model._meta})

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        campos = (*self.readonly_fields, 'estado_actual')
        if obj.instancias.exists():
            return (*campos, 'fecha_asignacion')
        return campos

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from .services import guardar_contrato

        guardar_contrato(contrato=obj, usuario=request.user, campos_editados=form.changed_data)



@admin.register(HistorialEstadoContrato)
class HistorialEstadoContratoAdmin(AdminOperativo):
    list_display = ('contrato', 'estado_anterior', 'estado_nuevo', 'resultado', 'solicitado_por', 'aprobado_por', 'fecha_solicitud')
    search_fields = ('contrato__numero_contrato', 'solicitado_por__username')
    list_filter = ('resultado', 'estado_nuevo')
    readonly_fields = (*tuple(field.name for field in HistorialEstadoContrato._meta.fields), 'documentos_vinculados')

    @admin.display(description='Soportes vinculados')
    def documentos_vinculados(self, obj):
        if not obj.pk:
            return '—'
        from django.utils.html import format_html_join
        return format_html_join(', ', '<a href="{}">{}</a>', ((reverse('descargar_documento', args=[s.documento_id]), s.documento.nombre_original) for s in obj.soportes.select_related('documento'))) or '—'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DocumentoContratoForm(forms.ModelForm):
    class Meta:
        model = DocumentoContrato
        fields = '__all__'
        widgets = {'archivo': forms.FileInput()}


@admin.register(DocumentoContrato)
class DocumentoContratoAdmin(AdminOperativo):
    form = DocumentoContratoForm
    list_display = ('nombre_original', 'contrato', 'tipo_documento', 'version', 'estado_revision', 'fecha_carga', 'activo')
    search_fields = ('nombre_original', 'contrato__numero_contrato', 'tipo_documento')
    list_filter = ('contrato', 'activo', 'tipo_documento', 'estado_revision')
    autocomplete_fields = ('contrato',)
    readonly_fields = ('cargado_por', 'fecha_carga', 'descarga_segura')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (*self.readonly_fields, 'archivo', 'contrato', 'estado_revision', 'nombre_original', 'version', 'anterior', 'activo', 'categoria', 'tipo_documento', 'descripcion', 'fecha_recepcion', 'motivo_rectificacion')
        return (*self.readonly_fields, 'estado_revision')

    def has_change_permission(self, request, obj=None):
        if obj:
            return False
        return super().has_change_permission(request, obj)

    @admin.display(description='Descarga')
    def descarga_segura(self, obj):
        if obj.pk and obj.archivo and obj.activo:
            return format_html('<a href="{}">Descargar documento</a>', reverse('descargar_documento', args=[obj.pk]))
        return 'No disponible'

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        if request.method != 'POST':
            return super().changeform_view(request, object_id, form_url, extra_context)
        from .archivos_transaccionales import escritura_con_archivo
        with escritura_con_archivo():
            return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        from .archivos_transaccionales import escritura_con_archivo
        if change:
            raise ValidationError('Use Revisión documental para registrar una rectificación con motivo y nueva versión.')
        obj.cargado_por = request.user
        with escritura_con_archivo() as guardar:
            entrada = obj.archivo.file
            entrada.seek(0)
            guardar(obj.archivo, obj.nombre_original or entrada.name, entrada.read())
            admin.ModelAdmin.save_model(self, request, obj, form, change)
            Auditoria.objects.create(usuario=request.user, accion='cargar_documento', entidad_afectada='DocumentoContrato',
                identificador_registro=str(obj.pk), datos_nuevos={'contrato_id':obj.contrato_id,'version':obj.version,'archivo':obj.archivo.name})

    def has_delete_permission(self, request, obj=None):
        return False
