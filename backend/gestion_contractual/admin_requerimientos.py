from django import forms
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import Contrato, PlantillaDocumento, TipoActa, TipoRequerimiento
from .actas import preparar_acta
from .competencia import calculo_verificado_vigente
from .requerimientos import cambiar_activacion_plantilla, exigir, generar_requerimiento, registrar_plantilla


class PlantillaForm(forms.Form):
    tipo = forms.ChoiceField(choices=TipoRequerimiento.choices, required=False)
    tipo_acta = forms.ModelChoiceField(queryset=TipoActa.objects.filter(activo=True), required=False)
    ficticia = forms.BooleanField(required=False, label='Plantilla ficticia no institucional')
    nombre = forms.CharField(max_length=150)
    archivo = forms.FileField()
    activar = forms.BooleanField(required=False, initial=True)

    def clean(self):
        datos = super().clean()
        tipo, acta = datos.get('tipo'), datos.get('tipo_acta')
        if bool(tipo) == bool(acta):
            raise ValidationError('Seleccione un tipo documental o un tipo de acta.')
        if acta:
            datos['tipo'] = acta.clave_documental
        return datos


class RequerimientoForm(forms.Form):
    tipo = forms.ChoiceField(choices=TipoRequerimiento.choices, required=False)
    tipo_acta = forms.ModelChoiceField(queryset=TipoActa.objects.all(), required=False)
    plantilla = forms.ModelChoiceField(queryset=PlantillaDocumento.objects.filter(activa=True, tipo_acta__activo=True), required=False)
    motivo = forms.CharField(required=False, label='Motivo de revisión del acta')
    anterior = forms.IntegerField(required=False, min_value=1, label='ID de la versión anterior')
    archivo = forms.FileField(required=False, label='DOCX revisado')
    operacion = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def clean(self):
        datos = super().clean()
        tipo, acta = datos.get('tipo'), datos.get('tipo_acta')
        if bool(tipo) == bool(acta):
            raise ValidationError('Seleccione un tipo documental o un tipo de acta.')
        if datos.get('plantilla') and not acta:
            raise ValidationError('La selección de plantilla corresponde a un tipo de acta.')
        return datos


@require_http_methods(['GET', 'POST'])
def plantillas(request, sitio):
    exigir(request.user, 'view_documentocontrato', 'add_documentocontrato')
    if request.method == 'POST' and request.POST.get('activar_version') in ('si', 'no'):
        clave = request.POST.get('plantilla_id', '')
        if not clave.isascii() or not clave.isdecimal() or len(clave) > 18:
            raise Http404
        plantilla = get_object_or_404(PlantillaDocumento, pk=int(clave))
        cambiar_activacion_plantilla(usuario=request.user, plantilla_id=plantilla.pk,
                                    activa=request.POST['activar_version'] == 'si')
        return redirect('admin:gestion_contractual_plantillas_requerimiento')
    form = PlantillaForm(request.POST, request.FILES) if request.method == 'POST' else PlantillaForm()
    if form.is_valid():
        try:
            registrar_plantilla(usuario=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            return redirect('admin:gestion_contractual_plantillas_requerimiento')
    return TemplateResponse(request, 'admin/gestion_contractual/requerimientos.html', {
        **sitio.each_context(request), 'title': 'Plantillas documentales', 'form': form,
        'plantillas': PlantillaDocumento.objects.select_related('tipo_acta').order_by('tipo', '-version'),
        'puede_activar': request.user.has_perm('gestion_contractual.change_documentocontrato'),
    })


@require_http_methods(['GET', 'POST'])
def requerimientos(request, sitio, contrato_id):
    exigir(request.user, 'view_contrato', 'view_documentocontrato', 'add_documentocontrato')
    contrato = get_object_or_404(Contrato, pk=contrato_id)
    es_sucesivo = contrato.estado_actual.codigo in (TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO)
    form = RequerimientoForm(request.POST, request.FILES) if request.method == 'POST' else RequerimientoForm(initial={
        'operacion': uuid4(), 'tipo': request.GET.get('tipo', contrato.estado_actual.codigo if es_sucesivo else None),
        'tipo_acta': request.GET.get('tipo_acta'), 'plantilla': request.GET.get('plantilla'),
    })
    campos_acta = None
    snapshot_observaciones = None
    snapshot_gcof19 = None
    if form.is_valid():
        acta, plantilla = form.cleaned_data['tipo_acta'], form.cleaned_data['plantilla']
        try:
            if request.POST.get('previsualizar'):
                if form.cleaned_data['tipo'] == TipoRequerimiento.PRIMERO and not acta:
                    from .primer_requerimiento import preparar_primer_requerimiento
                    campos_acta = preparar_primer_requerimiento(usuario=request.user, contrato_id=contrato.pk)
                elif form.cleaned_data['tipo'] in (TipoRequerimiento.SEGUNDO, TipoRequerimiento.TERCERO) and not acta:
                    from .requerimientos_sucesivos import preparar_requerimiento_sucesivo
                    campos_acta = preparar_requerimiento_sucesivo(usuario=request.user, contrato_id=contrato.pk,
                                                                 tipo=form.cleaned_data['tipo'])
                elif form.cleaned_data['tipo'] == TipoRequerimiento.OBSERVACIONES and not acta:
                    from .actas_observaciones import preparar_acta_observaciones
                    campos_acta, snapshot_observaciones = preparar_acta_observaciones(usuario=request.user, contrato_id=contrato.pk)
                elif form.cleaned_data['tipo'] == TipoRequerimiento.GCOF19 and not acta:
                    from .gcof19 import preparar_gcof19
                    campos_acta, snapshot_gcof19 = preparar_gcof19(usuario=request.user, contrato_id=contrato.pk)
                elif not acta or not plantilla:
                    raise ValidationError('Seleccione el tipo de acta y la plantilla para consultar los campos.')
                else:
                    campos_acta = preparar_acta(usuario=request.user, contrato_id=contrato.pk,
                                             tipo_acta_id=acta.pk, plantilla_id=plantilla.pk)
            else:
                generar_requerimiento(usuario=request.user, contrato_id=contrato.pk,
                                  tipo=form.cleaned_data['tipo'], archivo_revision=form.cleaned_data['archivo'],
                                  anterior_id=form.cleaned_data['anterior'], operacion=form.cleaned_data['operacion'],
                                  tipo_acta_id=acta.pk if acta else None, plantilla_id=plantilla.pk if plantilla else None,
                                  motivo=form.cleaned_data['motivo'])
        except (ValidationError, FileNotFoundError) as error:
            form.add_error(None, str(error) if isinstance(error, ValidationError) else 'El archivo de la plantilla no está disponible.')
        else:
            if campos_acta is None:
                return redirect('admin:gestion_contractual_requerimientos', contrato.pk)
    return TemplateResponse(request, 'admin/gestion_contractual/requerimientos.html', {
        **sitio.each_context(request), 'title': 'GENERACIÓN DE DOCUMENTOS', 'form': form, 'contrato': contrato,
        'borradores': contrato.borradores_requerimiento.select_related('plantilla__tipo_acta', 'documento', 'calculo_competencia').order_by('tipo', '-version'),
        'url_plantillas': reverse('admin:gestion_contractual_plantillas_requerimiento'),
        'campos_acta': campos_acta,
        'snapshot_observaciones': snapshot_observaciones,
        'snapshot_gcof19': snapshot_gcof19,
        'obligaciones_url': reverse('admin:gestion_contractual_obligaciones', args=[contrato.pk]),
        'revision_documental_url': reverse('admin:gestion_contractual_revision_documental', args=[contrato.pk]),
        'calculo_vigente_id': getattr(calculo_verificado_vigente(contrato), 'pk', None),
        'auditoria_url': reverse('admin:gestion_contractual_auditoria_changelist'),
        'etapa_requerimiento': contrato.estado_actual if es_sucesivo else None,
        'plantilla_etapa': PlantillaDocumento.objects.filter(tipo=contrato.estado_actual.codigo, activa=True).first() if es_sucesivo else None,
    })
