from django.db import migrations


ESTADOS = (
    ('primer-requerimiento', 'PRIMER REQUERIMIENTO', 15),
    ('revision-abogado-1', 'EN REVISIÓN DE ABOGADO GGC 1', 5),
    ('revision-supervisor-1', 'EN REVISIÓN DE SUPERVISOR 1', 5),
    ('segundo-requerimiento', 'SEGUNDO REQUERIMIENTO', 8),
    ('revision-abogado-2', 'EN REVISIÓN DE ABOGADO GGC 2', 5),
    ('revision-supervisor-2', 'EN REVISIÓN DE SUPERVISOR 2', 5),
    ('tercer-requerimiento', 'TERCER REQUERIMIENTO', 5),
    ('revision-abogado-3', 'EN REVISIÓN DE ABOGADO GGC 3', 5),
    ('revision-supervisor-3', 'EN REVISIÓN DE SUPERVISOR 3', 5),
    ('control-interno', 'REMITIDO A CONTROL INTERNO', 2),
    ('liquidado', 'LIQUIDADO', None),
    ('cierre', 'CIERRE', None),
)


def cargar_estados(apps, schema_editor):
    Estado = apps.get_model('gestion_contractual', 'EstadoContrato')
    gestor = Estado.objects.using(schema_editor.connection.alias)
    for orden, (codigo, nombre, plazo) in enumerate(ESTADOS, start=1):
        por_codigo = gestor.filter(codigo=codigo).first()
        por_nombre = gestor.filter(nombre__iexact=nombre).first()
        if por_codigo or por_nombre:
            if not por_codigo or not por_nombre or por_codigo.pk != por_nombre.pk or por_codigo.plazo_dias_habiles != plazo:
                raise RuntimeError(f'Conflicto en catálogo: revise el estado {codigo} sin borrar datos.')
            continue
        gestor.create(codigo=codigo, nombre=nombre, orden=orden, plazo_dias_habiles=plazo, activo=True)


class Migration(migrations.Migration):
    dependencies = [('gestion_contractual', '0005_estadocontrato_plazo_dias_habiles_and_more')]
    operations = [migrations.RunPython(cargar_estados, migrations.RunPython.noop)]
