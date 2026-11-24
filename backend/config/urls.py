from django.contrib import admin
from django.conf import settings
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_GET
from gestion_contractual.views import descargar_documento
@require_GET
def health(request):
    return JsonResponse({'status': 'ok', 'service': 'sigelc-backend'})
urlpatterns = [path('api/health/', health)]
if getattr(settings, 'SIGELC_DEMO', False):
    urlpatterns += [path('api/', include('gestion_contractual.api_demo_urls'))]
else:
    urlpatterns += [
        path('admin/', admin.site.urls),
        path('documentos/<int:pk>/descargar/', descargar_documento, name='descargar_documento'),
    ]
