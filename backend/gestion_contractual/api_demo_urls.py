from django.urls import path

from . import api_demo_views as views
from . import api_revision_inteligente as revision
from .api_actuaciones import ActuacionesView
from .api_alertas import AlertasContratoView, PrioridadTemporalView, RegistrarAlertasView
from .models import TipoRequerimiento


app_name = 'sigelc_demo'
urlpatterns = [
    path('alertas/registrar/', RegistrarAlertasView.as_view(), name='registrar-alertas'),
    path('contratos/<int:pk>/alertas/', AlertasContratoView.as_view(), name='alertas-contrato'),
    path('contratos/<int:pk>/alertas/registrar/', RegistrarAlertasView.as_view(), name='registrar-alertas-contrato'),
    path('contratos/<int:pk>/alertas/prioridad/', PrioridadTemporalView.as_view(), name='prioridad-temporal'),
    path('contratos/<int:pk>/actuaciones/', ActuacionesView.as_view(), name='actuaciones'),
    path('contratos/<int:pk>/revisiones/', revision.RevisionesView.as_view(), name='revisiones'),
    path('contratos/<int:pk>/revisiones/<int:rid>/', revision.RevisionDetalleView.as_view(), name='revision'),
    path('contratos/<int:pk>/revisiones/<int:rid>/analizar/', revision.RevisionAnalisisView.as_view(), name='revision-analizar'),
    path('contratos/<int:pk>/revisiones/<int:rid>/analizar-ia/', revision.RevisionIAView.as_view(), name='revision-analizar-ia'),
    path('contratos/<int:pk>/revisiones/<int:rid>/solicitar-claude/', revision.RevisionClaudeSolicitudView.as_view(), name='revision-solicitar-claude'),
    path('contratos/<int:pk>/revisiones/<int:rid>/decisiones/', revision.RevisionDecisionView.as_view(), name='revision-decisiones'),
    path('contratos/<int:pk>/revisiones/<int:rid>/generar/', revision.RevisionGenerarView.as_view(), name='revision-generar'),
    path('contratos/<int:pk>/revisiones/<int:rid>/fuentes/<int:fid>/descargar/', revision.RevisionFuenteView.as_view(), name='revision-fuente'),
    path('sesion/', views.SesionView.as_view(), name='sesion'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('catalogos/', views.CatalogosView.as_view(), name='catalogos'),
    path('contratos/', views.ContratosView.as_view(), name='contratos'),
    path('contratos/<int:pk>/', views.ContratoView.as_view(), name='contrato'),
    path('contratos/<int:pk>/seguimiento/', views.SeguimientoView.as_view(), name='seguimiento'),
    path('contratos/<int:pk>/vencimientos/', views.VencimientosView.as_view(), name='vencimientos'),
    path('contratos/<int:pk>/historial/', views.HistorialView.as_view(), name='historial'),
    path('contratos/<int:pk>/documentos/', views.DocumentosView.as_view(), name='documentos'),
    path('contratos/<int:pk>/documentos/<int:documento_id>/descargar/', views.DescargarView.as_view(), name='descargar'),
    path('contratos/<int:pk>/primer-requerimiento/', views.RequerimientoView.as_view(), name='primer-requerimiento'),
    path('contratos/<int:pk>/segundo-requerimiento/',
         views.RequerimientoView.as_view(tipo=TipoRequerimiento.SEGUNDO), name='segundo-requerimiento'),
    path('contratos/<int:pk>/tercer-requerimiento/',
         views.RequerimientoView.as_view(tipo=TipoRequerimiento.TERCERO), name='tercer-requerimiento'),
]
