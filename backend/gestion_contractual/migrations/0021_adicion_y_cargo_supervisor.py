from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('gestion_contractual', '0020_archivo_privado')]

    operations = [
        migrations.AddField(
            model_name='contrato',
            name='supervisor_cargo',
            field=models.CharField(blank=True, db_default='', max_length=255, verbose_name='cargo del supervisor'),
        ),
        migrations.AddField(
            model_name='contrato',
            name='valor_adicional',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=20, null=True,
                validators=[MinValueValidator(Decimal('0'))], verbose_name='valor adicional del contrato',
            ),
        ),
        migrations.AddConstraint(
            model_name='contrato',
            constraint=models.CheckConstraint(
                condition=models.Q(valor_adicional__isnull=True) | (
                    models.Q(valor_adicional__gte=0)
                    & models.Q(valor_actual=models.F('valor_inicial') + models.F('valor_adicional'))
                ),
                name='contrato_adicion_total_consistente',
            ),
        ),
    ]
