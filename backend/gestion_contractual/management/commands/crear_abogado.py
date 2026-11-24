from getpass import getpass

from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestion_contractual.models import Auditoria, PerfilUsuario


class Command(BaseCommand):
    help = 'Crea el único abogado operativo sin privilegios de superusuario.'

    def add_arguments(self, parser):
        parser.add_argument('--usuario', required=True)

    def handle(self, *args, **options):
        User = get_user_model()
        username = options['usuario'].strip()
        if not username or User.objects.filter(username=username).exists():
            raise CommandError('Nombre vacío o usuario existente; no se hicieron cambios.')
        if PerfilUsuario.objects.filter(abogado_operativo=True).exists():
            raise CommandError('Ya existe un abogado operativo; no se hicieron cambios.')
        primera = getpass('Contraseña: ')
        segunda = getpass('Confirme la contraseña: ')
        if primera != segunda:
            raise CommandError('Las contraseñas no coinciden.')
        try:
            password_validation.validate_password(primera, user=User(username=username))
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc
        codigos = []
        for modelo in ('entidadpublica', 'estadocontrato', 'transicionestado', 'contrato', 'documentocontrato'):
            codigos.extend(f'{accion}_{modelo}' for accion in ('view', 'add', 'change'))
        codigos.extend(['view_perfilusuario', 'view_historialestadocontrato', 'view_auditoria'])
        with transaction.atomic():
            usuario = User.objects.create_user(username=username, password=primera, is_staff=True, is_superuser=False)
            usuario.user_permissions.set(Permission.objects.filter(content_type__app_label='gestion_contractual', codename__in=codigos))
            PerfilUsuario.objects.create(usuario=usuario, abogado_operativo=True)
            Auditoria.objects.create(usuario=usuario, accion='crear_abogado_operativo', entidad_afectada='PerfilUsuario', identificador_registro=str(usuario.pk))
        self.stdout.write(self.style.SUCCESS('Abogado operativo creado; sin permisos de superusuario.'))
