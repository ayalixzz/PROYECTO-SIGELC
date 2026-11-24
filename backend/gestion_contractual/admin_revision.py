from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_http_methods

from .models import Contrato, EstadoRevision, ObservacionDocumental, TipoDocumental
from .requerimientos import exigir
from .revision import documentos_vigentes, recibir_documento, registrar_observacion, resumen_documental, revisar_documento


class FormOperacion(forms.Form):
    operacion = forms.UUIDField(widget=forms.HiddenInput)


@require_http_methods(['GET', 'POST'])
def expediente(request, sitio, contrato_id):
    exigir(request.user, 'view_contrato', 'view_documentocontrato')
    contrato = get_object_or_404(Contrato, pk=contrato_id)
    soportes = contrato.documentos.filter(generacion__isnull=True).order_by('pk')
    observaciones = contrato.observaciones_documentales.order_by('pk')

    class Recepcion(FormOperacion):
        categoria = forms.ModelChoiceField(queryset=TipoDocumental.objects.filter(activo=True))
        archivo = forms.FileField()
        fecha_recepcion = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
        descripcion = forms.CharField(required=False, widget=forms.Textarea)
        anterior = forms.ModelChoiceField(queryset=documentos_vigentes(contrato), required=False, label='Versión que reemplaza')
        motivo = forms.CharField(required=False, label='Motivo de rectificación')

    class Revision(forms.Form):
        documento = forms.ModelChoiceField(queryset=soportes)
        estado_esperado = forms.ChoiceField(choices=EstadoRevision.choices, label='Estado de revisión actual')
        estado = forms.ChoiceField(choices=EstadoRevision.choices, label='Nuevo estado de revisión')

    class Observacion(FormOperacion):
        descripcion = forms.CharField(widget=forms.Textarea)
        tipo = forms.ChoiceField(choices=ObservacionDocumental.Hallazgo.choices)
        estado_revision = forms.ChoiceField(choices=EstadoRevision.choices)
        evidencia = forms.CharField(required=False, widget=forms.Textarea)
        documentos = forms.ModelMultipleChoiceField(queryset=soportes, required=False)
        anterior = forms.ModelChoiceField(queryset=observaciones.filter(rectificacion__isnull=True), required=False, label='Observación que rectifica')
        motivo = forms.CharField(required=False)

    opciones = (
        ('recibir', 'Incorporar soporte o versión', Recepcion, recibir_documento, 'add_documentocontrato'),
        ('revisar', 'Registrar revisión de un soporte', Revision, revisar_documento, 'change_documentocontrato'),
        ('observar', 'Registrar o rectificar observación', Observacion, registrar_observacion, 'change_documentocontrato'),
    )
    formularios = []
    for accion, titulo, clase, servicio, permiso in opciones:
        seleccionado = request.method == 'POST' and request.POST.get('accion') == accion
        if seleccionado:
            exigir(request.user, permiso)
        if not request.user.has_perm(f'gestion_contractual.{permiso}'):
            continue
        form = (clase(request.POST, request.FILES, auto_id=f'{accion}_%s') if seleccionado
                else clase(initial={'operacion': uuid4()}, auto_id=f'{accion}_%s'))
        if seleccionado and form.is_valid():
            try:
                servicio(usuario=request.user, contrato_id=contrato.pk, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return redirect('admin:gestion_contractual_revision_documental', contrato.pk)
        formularios.append((accion, titulo, form))
    return TemplateResponse(request, 'admin/gestion_contractual/revision_documental.html', {
        **sitio.each_context(request), 'title': 'Revisión documental', 'contrato': contrato,
        'resumen': resumen_documental(contrato), 'soportes': soportes.select_related('categoria'),
        'observaciones': observaciones.select_related('responsable', 'anterior').prefetch_related('documentos'),
        'formularios': formularios,
    })
