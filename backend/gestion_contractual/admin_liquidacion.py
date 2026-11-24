from uuid import uuid4
from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import Contrato,PlantillaDocumento
from .liquidacion_datos import MODELOS,CAMPOS,CLAVES,registrar_insumo,vigentes,panorama
from .requerimientos import exigir


@require_http_methods(['GET','POST'])
def expediente(request,sitio,contrato_id):
    exigir(request.user,'view_contrato','view_documentocontrato')
    contrato=get_object_or_404(Contrato,pk=contrato_id)
    puede=request.user.has_perm('gestion_contractual.change_documentocontrato')
    forms_list=[]
    for tipo,modelo in MODELOS.items():
        if tipo=='pago':
            # La identidad/actor se completan bajo bloqueo en el servicio.
            # Un ModelForm ejecutaría clean() antes de disponer de esos datos.
            clase=type('EntradaPago',(forms.Form,),{k:modelo._meta.get_field(k).formfield() for k in CAMPOS[tipo]})
        else:
            clase=forms.modelform_factory(modelo,fields=CAMPOS[tipo])
        class Registro(clase):
            operacion=forms.UUIDField(widget=forms.HiddenInput)
            anterior=forms.ModelChoiceField(queryset=vigentes(modelo,contrato),required=False,label='Versión vigente que rectifica')
            motivo=forms.CharField(required=False,widget=forms.Textarea)
        form=Registro(request.POST if request.method=='POST' and request.POST.get('tipo')==tipo else None,
                      initial={'operacion':uuid4()},auto_id=tipo+'_%s')
        form.fields['documento'].queryset=contrato.documentos.filter(generacion__isnull=True)
        if tipo=='pago':
            from .models import OperacionPago
            form.fields['operacion_economica'].queryset=OperacionPago.objects.filter(contrato=contrato)
            form.fields['operacion_economica'].help_text='Seleccione el desembolso existente para agregar otra certificación/soporte; vacío crea una identidad nueva. Los posibles duplicados requieren revisión.'
        form.fields['clave'].help_text='Clave estable. Para nueva póliza/anexo/amparo use una clave nueva; para corrección seleccione la versión anterior.'
        if tipo=='dato':form.fields['clave']=forms.ChoiceField(choices=[(k,f'{c}: {k}') for c,keys in CLAVES.items() for k in sorted(keys)])
        if request.method=='POST' and request.POST.get('tipo')==tipo:
            exigir(request.user,'change_documentocontrato')
            if form.is_valid():
                try:registrar_insumo(usuario=request.user,contrato_id=contrato.pk,tipo=tipo,**form.cleaned_data)
                except ValidationError as exc:form.add_error(None,exc)
                else:return redirect('admin:gestion_contractual_liquidacion',contrato.pk)
        forms_list.append((tipo,modelo._meta.verbose_name,form))
    from .liquidacion import es_institucional
    plantillas=[]
    for plantilla in PlantillaDocumento.objects.filter(activa=True,tipo_acta__activo=True).select_related('tipo_acta'):
        with plantilla.archivo.open('rb') as archivo:
            if es_institucional(archivo.read()):plantillas.append(plantilla)
    return TemplateResponse(request,'admin/gestion_contractual/liquidacion.html',{
        **sitio.each_context(request),'title':'GENERAR ACTA DE LIQUIDACIÓN','contrato':contrato,'formularios':forms_list,
        'puede_registrar':puede,'panorama':panorama(contrato),'plantillas':plantillas,
        'historiales':[(tipo,modelo.objects.filter(contrato=contrato).select_related('documento','responsable','validado_por').order_by('clave','version')) for tipo,modelo in MODELOS.items()],
        'generar_url':reverse('admin:gestion_contractual_requerimientos',args=[contrato.pk]),
        'url_plantillas':reverse('admin:gestion_contractual_plantillas_requerimiento'),
        'auditoria_url':reverse('admin:gestion_contractual_auditoria_changelist')})
