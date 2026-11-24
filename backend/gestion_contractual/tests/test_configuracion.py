from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.models import Auditoria, PerfilUsuario


class ConfiguracionOperativaTest(TestCase):
    def test_creacion_unica_sin_superusuario(self):
        with patch('gestion_contractual.management.commands.crear_abogado.getpass', side_effect=['clave-compleja-prueba-43', 'clave-compleja-prueba-43']):
            call_command('crear_abogado', usuario='operador', stdout=StringIO())
        usuario = get_user_model().objects.get(username='operador')
        self.assertTrue(usuario.check_password('clave-compleja-prueba-43'))
        self.assertTrue(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(PerfilUsuario.objects.get(usuario=usuario).abogado_operativo)
        self.assertTrue(Auditoria.objects.filter(accion='crear_abogado_operativo').exists())
        self.assertTrue(self.client.login(username='operador', password='clave-compleja-prueba-43'))
        for modelo in ('contrato', 'documentocontrato', 'estadocontrato', 'transicionestado', 'entidadpublica'):
            self.assertTrue(usuario.has_perm(f'gestion_contractual.view_{modelo}'))
            self.assertTrue(usuario.has_perm(f'gestion_contractual.add_{modelo}'))
            self.assertTrue(usuario.has_perm(f'gestion_contractual.change_{modelo}'))
            self.assertEqual(self.client.get(reverse(f'admin:gestion_contractual_{modelo}_changelist')).status_code, 200)
        respuesta = self.client.post(reverse('admin:gestion_contractual_entidadpublica_add'), {'nombre': 'Entidad de prueba', 'nit': 'NIT-PRUEBA', 'activo': 'on'})
        self.assertEqual(respuesta.status_code, 302, getattr(respuesta, 'context_data', None))
        self.assertTrue(Auditoria.objects.filter(accion='crear', entidad_afectada='gestion_contractual.EntidadPublica').exists())
        with self.assertRaises(CommandError):
            call_command('crear_abogado', usuario='otro', stdout=StringIO())
