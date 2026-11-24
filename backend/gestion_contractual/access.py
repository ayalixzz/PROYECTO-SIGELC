from .models import PerfilUsuario


def es_abogado_operativo(usuario):
    return bool(usuario.is_authenticated and usuario.is_active and PerfilUsuario.objects.filter(usuario_id=usuario.pk, activo=True, abogado_operativo=True).exists())
