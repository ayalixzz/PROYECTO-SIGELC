from django.db import migrations


def crear_grupos(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for nombre in ('Administrador', 'Abogado responsable', 'Supervisor contractual', 'Profesional de apoyo', 'Consulta'):
        Group.objects.using(schema_editor.connection.alias).get_or_create(name=nombre)


class Migration(migrations.Migration):
    dependencies = [('gestion_contractual', '0002_contrato_documentocontrato_historialestadocontrato_and_more'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(crear_grupos, migrations.RunPython.noop)]
