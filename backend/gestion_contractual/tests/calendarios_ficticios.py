"""Explicit approval fixtures only; never loaded by application startup/migrations."""
from uuid import uuid4
from gestion_contractual.calendarios_servicios import proponer, aprobar, vigente


def aprobar_ficticio(usuario, anio, excepciones=()):
    actual=vigente(anio)
    p=proponer(anio=anio,usuario=usuario,fuente='https://example.invalid/FICTICIO',motivo='PROPUESTA FICTICIA',
               operacion=uuid4(),anterior_id=getattr(actual,'pk',None),excepciones=excepciones)
    return aprobar(propuesta_id=p.pk,usuario=usuario,operacion=uuid4(),confirmado=True,fundamento='APROBACIÓN FICTICIA, no institucional')


def aprobar_regresion(usuario):
    for anio in (2025,2026,2027):
        aprobar_ficticio(usuario,anio)
