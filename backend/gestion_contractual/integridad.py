"""Control optimista y serialización de valores auditables."""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from django.core.exceptions import ValidationError


class ConflictoEdicion(ValidationError):
    def __init__(self):
        super().__init__('Conflicto de edición: el registro cambió. Recargue y revise los datos antes de guardar.')


def valores(obj):
    result = {}
    for field in obj._meta.concrete_fields:
        value = getattr(obj, field.attname)
        result[field.name] = value.isoformat() if isinstance(value, (date, datetime)) else str(value) if isinstance(value, (Decimal, UUID)) else value
    return result
