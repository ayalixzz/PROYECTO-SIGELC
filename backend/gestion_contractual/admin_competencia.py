from uuid import uuid4

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.views.decorators.http import require_http_methods

from .competencia import datos_contrato, registrar_calculo
from .models import Contrato
from .requerimientos import exigir


SI_NO = [('', 'Por determinar'), ('si', 'Sí'), ('no', 'No')]
FECHAS = {
    'fecha_base': 'Fecha base',
    'fin_bilateral': 'Vencimiento bilateral',
    'fin_unilateral': 'Vencimiento unilateral',
    'inicio_judicial': 'Inicio judicial',
    'fin_judicial': 'Vencimiento judicial',
    'limite_competencia': 'Límite estimado para liquidar',
}


def campo_fecha(label):
    return forms.DateField(required=False, label=label,
                           widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))


class PropuestaForm(forms.Form):
    fecha_terminacion = forms.DateField(required=False, widget=forms.HiddenInput)
    sujeto = forms.ChoiceField(choices=SI_NO, required=False, label='¿Está sujeto a liquidación?')
    regimen = forms.ChoiceField(required=False, label='Régimen confirmado para la proyección', choices=[
        ('', 'Por determinar'), ('art11_cpaca', 'Artículo 11 de Ley 1150 y CPACA'), ('otro', 'Otro régimen'),
    ])
    origen = forms.ChoiceField(required=False, label='Origen de la fecha base', choices=[
        ('', 'Por determinar'), ('ejecucion', 'Expiración de la ejecución (fecha contractual)'),
        ('efectiva', 'Terminación efectiva'), ('acto', 'Acto administrativo'), ('acuerdo', 'Acuerdo'),
        ('otra', 'Otra fecha jurídicamente relevante'),
    ])
    fecha_efectiva = campo_fecha('Terminación efectiva')
    fecha_referencia = campo_fecha('Fecha del acto, acuerdo u otra referencia')
    soporte_terminacion = forms.ModelChoiceField(queryset=None, required=False, label='Soporte de terminación')
    pacto = forms.ChoiceField(choices=SI_NO, required=False, label='¿Hay plazo bilateral pactado?')
    cantidad = forms.IntegerField(min_value=1, max_value=10000, required=False, label='Duración del pacto')
    unidad = forms.ChoiceField(required=False, choices=[
        ('', 'Por determinar'), ('dias_calendario', 'Días calendario'), ('meses', 'Meses'),
        ('anios', 'Años'), ('dias_habiles', 'Días hábiles (revisión jurídica)'),
    ])
    referencia_pacto = campo_fecha('Fecha de referencia del pacto')
    liquidacion = forms.ChoiceField(required=False, label='Liquidación previa', choices=[
        ('', 'Por determinar'), ('ninguna', 'Ninguna'), ('bilateral', 'Bilateral'), ('unilateral', 'Unilateral'),
    ])
    fecha_liquidacion = campo_fecha('Fecha del acta o acto de liquidación')
    firma_acta = campo_fecha('Fecha de firma bilateral')
    ejecutoria_acto = campo_fecha('Fecha de ejecutoria unilateral')
    incidencias = forms.ChoiceField(choices=SI_NO, required=False, label='¿Existen incidencias, demanda, conciliación o suspensiones?')
    referencias = forms.CharField(required=False, widget=forms.Textarea, label='Referencias documentales y cláusulas')
    observaciones = forms.CharField(required=False, widget=forms.Textarea, label='Observaciones jurídicas')
    motivo = forms.CharField(required=False, label='Motivo de la actualización')
    operacion = forms.UUIDField(widget=forms.HiddenInput)
    anterior_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, contrato, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['soporte_terminacion'].queryset = contrato.documentos.all()


class VerificacionForm(forms.Form):
    operacion = forms.UUIDField(widget=forms.HiddenInput)
    anterior_id = forms.IntegerField(widget=forms.HiddenInput)
    fundamento = forms.CharField(widget=forms.Textarea, label='Fundamento jurídico')
    confirmar = forms.BooleanField(label='Confirmo haber revisado el expediente y el fundamento')


class RectificacionForm(VerificacionForm):
    motivo = forms.CharField()
    fecha_base = campo_fecha(FECHAS['fecha_base'])
    fin_bilateral = campo_fecha(FECHAS['fin_bilateral'])
    fin_unilateral = campo_fecha(FECHAS['fin_unilateral'])
    inicio_judicial = campo_fecha(FECHAS['inicio_judicial'])
    fin_judicial = campo_fecha(FECHAS['fin_judicial'])
    limite_competencia = campo_fecha(FECHAS['limite_competencia'])


@require_http_methods(['GET', 'POST'])
def expediente(request, sitio, contrato_id):
    exigir(request.user, 'view_contrato', 'change_contrato')
    contrato = get_object_or_404(Contrato, pk=contrato_id)
    if contrato.abogado_responsable_id != request.user.pk:
        raise PermissionDenied
    versiones = contrato.calculos_competencia.select_related('creado_por', 'verificado_por')
    previo = versiones.first()
    inicial = {**(previo.insumos if previo else {}), **datos_contrato(contrato),
               'anterior_id': previo.pk if previo else None}
    formularios = []
    for accion, titulo, clase in (
        ('calcular', 'Calcular propuesta', PropuestaForm),
        ('rectificar', 'Guardar rectificación jurídica', RectificacionForm),
        ('verificar', 'Verificar como abogado', VerificacionForm),
    ):
        if accion != 'calcular' and not previo:
            continue
        seleccionado = request.method == 'POST' and request.POST.get('accion') == accion
        opciones = {'contrato': contrato} if accion == 'calcular' else {}
        if seleccionado:
            form = clase(request.POST, auto_id=f'{accion}_%s', **opciones)
        else:
            inicial_form = {**inicial, 'operacion': uuid4()}
            if accion == 'rectificar':
                inicial_form.update({k: previo.resultado.get(k) for k in FECHAS})
            form = clase(initial=inicial_form, auto_id=f'{accion}_%s', **opciones)
        if seleccionado and form.is_valid():
            datos = form.cleaned_data.copy()
            operacion = datos.pop('operacion')
            anterior_id = datos.pop('anterior_id')
            fundamento = datos.pop('fundamento', '')
            motivo = datos.pop('motivo', '')
            datos.pop('confirmar', None)
            normalizados = {k: v.isoformat() if hasattr(v, 'isoformat') else v.pk if hasattr(v, 'pk') else v
                            for k, v in datos.items()}
            try:
                registrar_calculo(contrato_id=contrato.pk, usuario=request.user, accion=accion,
                                  operacion=operacion, anterior_id=anterior_id, fundamento=fundamento,
                                  motivo=motivo, datos=normalizados if accion == 'calcular' else None,
                                  rectificacion=normalizados if accion == 'rectificar' else None)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return redirect('admin:gestion_contractual_competencia', contrato.pk)
        formularios.append((accion, titulo, form))
    from .calendarios_servicios import evaluar_vigencia
    return TemplateResponse(request, 'admin/gestion_contractual/competencia.html', {
        **sitio.each_context(request), 'title': 'Cálculo jurídico de liquidación', 'contrato': contrato,
        'versiones': versiones, 'formularios': formularios,
        'vigencia_calendario': evaluar_vigencia(previo) if previo else None,
        'fechas': FECHAS, 'insumos_desactualizados': previo and any(
            previo.insumos.get(k) != v for k, v in datos_contrato(contrato).items()),
    })
