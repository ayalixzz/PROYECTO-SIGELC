"""Proyección de plazos y registro de hitos observados en el expediente."""
from datetime import date
from hashlib import sha256

from django.core.exceptions import ValidationError
from django.db import transaction

from .actuaciones_models import ActuacionContractual
from .alertas_politica import PRIORIDAD, evaluar_alertas
from .api_demo_datos import vencimiento_datos
from .competencia import calculo_verificado_vigente
from .models import Auditoria, PrioridadTemporalContrato, RegistroAlertaContrato


def ultimo_memorando(contrato, *, ver_soportes):
    memorando = contrato.actuaciones.filter(tipo=ActuacionContractual.Tipo.MEMORANDO).first()
    if memorando is None:
        return None
    return {
        'id': memorando.pk, 'titulo': memorando.titulo, 'radicado': memorando.radicado,
        'fecha': memorando.fecha.isoformat(),
        'documento_id': memorando.documento_id if ver_soportes else None,
    }


def datos_expediente(contrato):
    return {
        'contrato_id': contrato.pk, 'numero_contrato': contrato.numero_contrato,
        'anio_contractual': contrato.anio_contractual,
    }


def _limite_competencia(contrato):
    vigente = calculo_verificado_vigente(contrato)
    if not vigente:
        return None, 'Sin fecha límite de competencia verificada y vigente.'
    valor = vigente.resultado.get('limite_competencia')
    if valor:
        try:
            fecha = date.fromisoformat(valor)
        except (ValueError, TypeError):
            return None, 'La fecha registrada requiere revisión.'
        return {
            'fecha': fecha.isoformat(),
            'fuente': {'tipo': 'calculo_competencia', 'id': vigente.pk, 'version': vigente.version},
        }, None
    if vigente.insumos.get('sujeto') == 'no' or vigente.insumos.get('liquidacion') in ('bilateral', 'unilateral'):
        return None, None
    return None, 'La versión verificada no contiene una fecha límite de competencia.'


def evaluar_expediente(contrato, hoy, instancia, vencimiento):
    if contrato.estado_actual.codigo in ('liquidado', 'cierre'):
        return [], [], None
    referencia = datos_expediente(contrato)
    revisiones = []
    limite, motivo = _limite_competencia(contrato)
    if motivo:
        revisiones.append({**referencia, 'tipo': 'competencia',
                           'motivo': motivo})

    if vencimiento and vencimiento['estado'] != 'confirmado':
        revisiones.append({**referencia, 'tipo': 'operativa', 'motivo': vencimiento['advertencia']})

    avisos = evaluar_alertas(hoy=hoy, vencimiento_operativo=vencimiento,
                            fecha_juridica=limite['fecha'] if limite else None)
    for aviso in avisos:
        if aviso['tipo'] == 'competencia':
            fuente = limite['fuente']
        else:
            fuente = {'tipo': 'instancia_seguimiento', 'id': instancia.pk}
        clave = f"1:{fuente['id']}:{aviso['clave']}"
        aviso.update(referencia, fuente=fuente, clave=sha256(clave.encode()).hexdigest())
    return avisos, revisiones, limite


def estado_expediente(contrato, hoy, instancia):
    return evaluar_expediente(contrato, hoy, instancia, vencimiento_datos(instancia))


def prioridad_efectiva(contrato, hoy, avisos):
    base = {'nivel': 'normal', 'origen': 'ninguno', 'motivo': 'Sin alerta vigente en las ventanas de aviso.'}
    if avisos:
        actual = avisos[0]
        base = {'nivel': actual['prioridad'], 'origen': actual['tipo'], 'motivo': actual['titulo']}
    manual = contrato.prioridades_temporales.first()
    if (manual and manual.vence >= hoy and base['nivel'] != 'critica'
            and contrato.estado_actual.codigo not in ('liquidado', 'cierre')):
        return {'nivel': 'alta', 'origen': 'manual', 'motivo': manual.motivo,
                'vence': manual.vence.isoformat()}
    return base


def ficha_alertas(contrato, hoy, instancia, *, puede_registrar, ver_soportes):
    avisos, revisiones, limite = estado_expediente(contrato, hoy, instancia)
    activas = {aviso['clave'] for aviso in avisos}
    historial = [
        {**registro.instantanea, 'id': registro.pk, 'registrada_en': registro.registrada_en.isoformat(),
         'activa': registro.clave in activas}
        for registro in contrato.alertas_registradas.all()
    ]
    return {
        'alertas': avisos, 'revisiones': revisiones, 'historial': historial, 'fecha_competencia': limite,
        'prioridad': prioridad_efectiva(contrato, hoy, avisos),
        'ultimo_memorando': ultimo_memorando(contrato, ver_soportes=ver_soportes),
        'puede_registrar': puede_registrar and contrato.estado_actual.codigo not in ('liquidado', 'cierre'),
    }


def resumen_panel(avisos, revisiones, prioridades):
    avisos.sort(key=lambda alerta: (
        PRIORIDAD[alerta['prioridad']], alerta['fecha_objetivo'], alerta['numero_contrato'],
    ))
    grupos = {
        'juridicas': [alerta for alerta in avisos if alerta['tipo'] == 'competencia'],
        'operativas': [alerta for alerta in avisos if alerta['tipo'] == 'operativa'],
        'requieren_revision': revisiones,
    }
    return {
        **grupos, 'totales': {grupo: len(filas) for grupo, filas in grupos.items()},
        'prioridades_temporales': sorted(prioridades, key=lambda fila: (fila['vence'], fila['numero_contrato'])),
    }


def registrar_hitos(contrato, usuario, hoy, instancia):
    avisos, _, _ = estado_expediente(contrato, hoy, instancia)
    nuevas = 0
    for aviso in avisos:
        _, nueva = RegistroAlertaContrato.objects.get_or_create(
            contrato=contrato, clave=aviso['clave'],
            defaults={'usuario': usuario, 'instantanea': aviso},
        )
        nuevas += nueva
    return nuevas


@transaction.atomic
def registrar_prioridad_temporal(contrato, usuario, hoy, *, operacion, motivo, vence):
    if contrato.estado_actual.codigo in ('liquidado', 'cierre'):
        raise ValidationError('El expediente ya está cerrado para el seguimiento.')
    registro, nuevo = PrioridadTemporalContrato.objects.get_or_create(
        operacion=operacion,
        defaults={'contrato': contrato, 'usuario': usuario, 'motivo': motivo, 'vence': vence},
    )
    if (registro.contrato_id, registro.usuario_id, registro.motivo, registro.vence) != (
        contrato.pk, usuario.pk, motivo, vence,
    ):
        raise ValidationError('Identificador de operación en uso.')
    if nuevo:
        if vence < hoy:
            raise ValidationError('La fecha de expiración debe ser hoy o posterior.')
        Auditoria.objects.create(
            usuario=usuario, accion='registrar_prioridad_temporal', entidad_afectada='PrioridadTemporalContrato',
            identificador_registro=str(registro.pk),
            datos_nuevos={'contrato_id': contrato.pk, 'motivo': motivo, 'vence': vence.isoformat()},
        )
