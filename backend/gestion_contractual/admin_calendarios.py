"""Provisional Admin interface. All writes delegate to audited services."""
from uuid import uuid4
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .access import es_abogado_operativo
from .calendarios_servicios import aprobar, comparar, confirmar_vencimiento, estado_vigencia, proponer, vigente
from .models import CalendarioVerificado


class PropuestaCalendarioForm(forms.Form):
    anio = forms.IntegerField(min_value=1984, max_value=9998)
    fuente = forms.URLField(max_length=500)
    motivo = forms.CharField(widget=forms.Textarea)
    excepciones = forms.JSONField(initial=list, required=False, widget=forms.Textarea,
        help_text='Lista de objetos: fecha (AAAA-MM-DD), habil (true/false), motivo y fuente https. No deroga festivos nacionales.')
    anterior_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    operacion = forms.UUIDField(widget=forms.HiddenInput)


class AprobacionCalendarioForm(forms.Form):
    fundamento = forms.CharField(widget=forms.Textarea)
    confirmado = forms.BooleanField(label='Revisé las fuentes, excepciones, cobertura anual y política operativa lunes a viernes. No valida calendario judicial.')
    operacion = forms.UUIDField(widget=forms.HiddenInput)


class ConfirmacionPlazoForm(forms.Form):
    evaluacion_id = forms.IntegerField(min_value=1)
    motivo = forms.CharField(widget=forms.Textarea)
    confirmado = forms.BooleanField(label='Confirmo la revisión del vencimiento abierto. No modifica actuaciones ni notificaciones históricas.')
    operacion = forms.UUIDField(widget=forms.HiddenInput)


@admin.register(CalendarioVerificado)
class CalendarioAdmin(admin.ModelAdmin):
    list_display = ('anio','version','vigencia','responsable','aprobado_en','revisar')
    list_filter = ('anio','estado')
    change_list_template = 'admin/gestion_contractual/calendarios_lista.html'
    actions = None

    def has_view_permission(self, request, obj=None):
        return es_abogado_operativo(request.user) and request.user.has_perm('gestion_contractual.view_contrato')

    def has_module_permission(self, request): return self.has_view_permission(request)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def get_readonly_fields(self, request, obj=None): return [f.name for f in self.model._meta.fields]

    @admin.display(description='Vigencia')
    def vigencia(self, obj): return estado_vigencia(obj)

    @admin.display(description='Revisión e impacto')
    def revisar(self, obj):
        return format_html('<a href="{}">Revisar versión y expediente</a>',reverse('admin:sigelc_calendario_revisar',args=[obj.pk]))

    def get_urls(self):
        return [path('preparar/',self.admin_site.admin_view(self.preparar),name='sigelc_calendario_preparar'),
                path('<int:pk>/revisar/',self.admin_site.admin_view(self.detalle),name='sigelc_calendario_revisar')] + super().get_urls()

    def preparar(self, request):
        if not self.has_view_permission(request) or not request.user.has_perm('gestion_contractual.change_contrato'):
            raise PermissionDenied
        anio = request.GET.get('anio','')
        anterior = vigente(int(anio)) if anio.isdigit() and 1984<=int(anio)<=9998 else None
        form=PropuestaCalendarioForm(request.POST or None,initial={'anio':anio,'anterior_id':getattr(anterior,'pk',None),
            'operacion':uuid4(),'excepciones':anterior.excepciones if anterior else []})
        if request.method=='POST' and form.is_valid():
            try:
                p=proponer(usuario=request.user,**{**form.cleaned_data,'excepciones':form.cleaned_data['excepciones'] or []})
            except ValidationError as error: form.add_error(None,error)
            else: return redirect('admin:sigelc_calendario_revisar',p.pk)
        return TemplateResponse(request,'admin/gestion_contractual/calendario_revision.html',{
            **self.admin_site.each_context(request),'title':'Preparar calendario: propuesta sin aprobación automática',
            'form':form,'accion':'proponer'})

    def detalle(self, request, pk):
        if not self.has_view_permission(request): raise PermissionDenied
        calendario=get_object_or_404(CalendarioVerificado,pk=pk)
        accion=request.POST.get('accion')
        form=AprobacionCalendarioForm(request.POST if accion=='aprobar' else None,initial={'operacion':uuid4()})
        confirmacion=ConfirmacionPlazoForm(request.POST if accion=='confirmar' else None,initial={'operacion':uuid4()})
        if request.method=='POST':
            if not request.user.has_perm('gestion_contractual.change_contrato'): raise PermissionDenied
            seleccionado=form if accion=='aprobar' else confirmacion if accion=='confirmar' else None
            if seleccionado is None: raise ValidationError('Acción desconocida.')
            if seleccionado.is_valid():
                try:
                    if accion=='aprobar':
                        n=aprobar(propuesta_id=pk,usuario=request.user,**form.cleaned_data)
                        return redirect('admin:sigelc_calendario_revisar',n.pk)
                    from .models import EvaluacionCalendario
                    if not EvaluacionCalendario.objects.filter(pk=confirmacion.cleaned_data['evaluacion_id'],calendario=calendario).exists():
                        raise ValidationError('Seleccione una evaluación de esta versión.')
                    confirmar_vencimiento(usuario=request.user,**confirmacion.cleaned_data)
                    return redirect('admin:sigelc_calendario_revisar',pk)
                except ValidationError as error: seleccionado.add_error(None,error)
        comparacion=comparar(calendario.anterior,calendario)
        if request.GET.get('comparar_con','').isdigit():
            otra=get_object_or_404(CalendarioVerificado,pk=request.GET['comparar_con'],anio=calendario.anio)
            comparacion=comparar(otra,calendario)
        return TemplateResponse(request,'admin/gestion_contractual/calendario_revision.html',{
            **self.admin_site.each_context(request),'title':'Calendarios y vigencia jurídica','calendario':calendario,
            'vigencia':estado_vigencia(calendario),'comparacion':comparacion,
            'evaluaciones':calendario.evaluaciones.select_related('instancia__contrato','calculo__contrato'),
            'form':form if calendario.estado=='propuesta' else None,'accion':'aprobar','confirmacion':confirmacion})
