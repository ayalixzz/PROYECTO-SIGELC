from django.db import migrations


SECUENCIA = (
    'primer-requerimiento', 'revision-abogado-1', 'revision-supervisor-1',
    'segundo-requerimiento', 'revision-abogado-2', 'revision-supervisor-2',
    'tercer-requerimiento', 'revision-abogado-3', 'revision-supervisor-3',
    'control-interno',
)


def crear_transiciones(apps, schema_editor):
    Estado = apps.get_model('gestion_contractual', 'EstadoContrato')
    Transicion = apps.get_model('gestion_contractual', 'TransicionEstado')
    alias = schema_editor.connection.alias
    for origen, destino in zip(SECUENCIA, SECUENCIA[1:]):
        a = Estado.objects.using(alias).get(codigo=origen)
        b = Estado.objects.using(alias).get(codigo=destino)
        Transicion.objects.using(alias).get_or_create(origen=a, destino=b, defaults={'activa': True})


class Migration(migrations.Migration):
    dependencies = [('gestion_contractual', '0006_catalogo_seguimiento')]
    operations = [migrations.RunPython(crear_transiciones, migrations.RunPython.noop)]
