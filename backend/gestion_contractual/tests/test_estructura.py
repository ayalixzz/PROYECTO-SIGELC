from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from gestion_contractual.models import Auditoria, EntidadPublica, EstadoContrato, TransicionEstado


class EstructuraTest(TestCase):
    def test_solo_una_entidad_activa(self):
        EntidadPublica.objects.create(nombre='Entidad A', nit='1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            EntidadPublica.objects.create(nombre='Entidad B', nit='2')
        EntidadPublica.objects.create(nombre='Entidad histórica', nit='3', activo=False)

    def test_transicion_unica_y_sin_bucle(self):
        uno = EstadoContrato.objects.create(codigo='uno', nombre='Uno')
        dos = EstadoContrato.objects.create(codigo='dos', nombre='Dos')
        TransicionEstado.objects.create(origen=uno, destino=dos)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransicionEstado.objects.create(origen=uno, destino=dos)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransicionEstado.objects.create(origen=uno, destino=uno)

    def test_auditoria_no_se_edita_ni_elimina_por_instancia(self):
        usuario = get_user_model().objects.create_user(username='prueba')
        registro = Auditoria.objects.create(usuario=usuario, accion='prueba', entidad_afectada='prueba', identificador_registro='1')
        registro.accion = 'cambio'
        with self.assertRaises(ValidationError):
            registro.save()
        with self.assertRaises(ValidationError):
            registro.delete()
