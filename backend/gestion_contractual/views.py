from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .access import es_abogado_operativo
from .models import Auditoria, DocumentoContrato


@login_required(login_url='/admin/login/')
@require_GET
def descargar_documento(request, pk):
    if not es_abogado_operativo(request.user) or not request.user.has_perm('gestion_contractual.view_documentocontrato'):
        raise PermissionDenied
    documento = get_object_or_404(DocumentoContrato, pk=pk, activo=True)
    try:
        archivo = documento.archivo.open('rb')
    except (FileNotFoundError, ValueError):
        raise Http404('Documento no disponible.')
    try:
        Auditoria.objects.create(usuario=request.user, accion='descargar_documento', entidad_afectada='DocumentoContrato', identificador_registro=str(documento.pk), datos_nuevos={'contrato_id': documento.contrato_id})
    except Exception:
        archivo.close()
        raise
    respuesta = FileResponse(archivo, as_attachment=True, filename=Path(documento.nombre_original).name, content_type='application/octet-stream')
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta
