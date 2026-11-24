from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import Contrato, ObligacionContractual
from .obligaciones import registrar_obligacion, vigentes
from .requerimientos import exigir


@require_http_methods(['GET', 'POST'])
def obligaciones(request, sitio, contrato_id):
    exigir(request.user, 'view_contrato', 'view_documentocontrato')
    contrato = get_object_or_404(Contrato, pk=contrato_id)
    fuentes = contrato.documentos.filter(generacion__isnull=True)
    hallazgos = contrato.observaciones_documentales.all()

    class Registro(forms.Form):
        numero = forms.IntegerField(min_value=1, label='Número u orden')
        tipo = forms.ChoiceField(choices=ObligacionContractual.Tipo.choices)
        descripcion = forms.CharField(widget=forms.Textarea, label='Descripción contractual')
        actividad = forms.CharField(required=False, widget=forms.Textarea, label='Actividad informada')
        producto = forms.CharField(required=False, widget=forms.Textarea, label='Producto informado')
        comentario_supervisor = forms.CharField(required=False, widget=forms.Textarea, label='Comentario aportado por el supervisor')
        comentario_verificado = forms.BooleanField(required=False, label='Atribución del comentario al supervisor verificada con soporte')
        porcentaje = forms.DecimalField(required=False, min_value=0, max_value=100, max_digits=5, decimal_places=2,
                                       help_text='Vacío significa pendiente; cero es un dato. Requiere soporte y verificación documental.')
        estado_verificacion = forms.ChoiceField(choices=ObligacionContractual.Verificacion.choices)
        fundamento = forms.CharField(required=False, widget=forms.Textarea, label='Referencia documental de verificación (documento, página o ubicación)')
        documentos = forms.ModelMultipleChoiceField(queryset=fuentes, required=False, label='Soportes existentes del contrato')
        observaciones_abogado = forms.ModelMultipleChoiceField(queryset=hallazgos, required=False, label='Referencias a hallazgos del abogado; separadas de comentarios del supervisor')
        anterior = forms.ModelChoiceField(queryset=vigentes(contrato), required=False, label='Obligación vigente que rectifica')
        motivo = forms.CharField(required=False, widget=forms.Textarea, label='Motivo de rectificación')
        operacion = forms.UUIDField(widget=forms.HiddenInput)

    inicial = {'operacion': uuid4(), 'estado_verificacion':'pendiente'}
    if request.GET.get('rectificar'):
        ob = get_object_or_404(vigentes(contrato), pk=request.GET['rectificar'])
        inicial.update({k:getattr(ob,k) for k in ('numero','tipo','descripcion','actividad','producto','comentario_supervisor',
                        'comentario_verificado','porcentaje','estado_verificacion','fundamento')})
        inicial.update(anterior=ob.pk, documentos=list(ob.documentos.values_list('pk',flat=True)),
                       observaciones_abogado=list(ob.observaciones_abogado.values_list('pk',flat=True)))
    puede_registrar = request.user.has_perm('gestion_contractual.change_documentocontrato')
    if request.method == 'POST': exigir(request.user, 'change_documentocontrato')
    form = Registro(request.POST) if request.method == 'POST' else Registro(initial=inicial)
    if request.method == 'POST' and form.is_valid():
        try:
            registrar_obligacion(usuario=request.user, contrato_id=contrato.pk, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect('admin:gestion_contractual_obligaciones', contrato.pk)
    return TemplateResponse(request,'admin/gestion_contractual/obligaciones.html',{
        **sitio.each_context(request), 'title':'Obligaciones y preparación GCOF19', 'contrato':contrato,
        'form':form,'puede_registrar':puede_registrar,'vigentes':vigentes(contrato),
        'historial':contrato.obligaciones.select_related('responsable','anterior').prefetch_related('documentos','observaciones_abogado').order_by('tipo','numero','version'),
        'generar_url':reverse('admin:gestion_contractual_requerimientos',args=[contrato.pk])+'?tipo=gcof19',
        'auditoria_url':reverse('admin:gestion_contractual_auditoria_changelist'),
    })
