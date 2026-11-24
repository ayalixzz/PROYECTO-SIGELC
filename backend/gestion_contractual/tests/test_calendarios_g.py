from datetime import date, timedelta
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction, IntegrityError
from django.test import TestCase
from django.urls import reverse

from gestion_contractual.calendario import (DiasCalendario, calcular_vencimiento, calendario_disponible,
    calendario_nacional, es_dia_habil, preparar_nacional)
from gestion_contractual.calendarios_servicios import (aprobar, comparar, confirmar_vencimiento,
    estado_vigencia, evaluar_dependencias, proponer, revisar_instancia, vencimiento_documental, vigente)
from gestion_contractual.competencia import (Estado, FECHAS_RESULTADO, evaluar, fecha_confirmada,
    registrar_calculo, sumar_anios, sumar_meses)
from gestion_contractual.models import Auditoria, CalendarioVerificado, CalculoCompetencia, DocumentoGenerado, EvaluacionCalendario, InstanciaSeguimiento, TipoActa
from gestion_contractual.services import iniciar_seguimiento
from gestion_contractual.requerimientos import generar_requerimiento, registrar_plantilla
from .test_requerimientos import RequerimientosTest, docx
from .calendarios_ficticios import aprobar_ficticio


def excepcion(fecha, habil=False):
    return {'fecha':fecha,'habil':habil,'motivo':'ACTO FICTICIO, exclusivamente ensayo', 'fuente':'https://example.invalid/ACTO-FICTICIO'}


class CalendariosGTest(TestCase):
    setUpTestData=classmethod(RequerimientosTest.setUpTestData.__func__)
    setUp=RequerimientosTest.setUp

    def propuesta(self, anio=2026, **otros):
        args=dict(anio=anio,usuario=self.abogado,fuente='https://example.invalid/FICTICIO',motivo='PROPUESTA FICTICIA',
                  operacion=uuid4(),anterior_id=getattr(vigente(anio),'pk',None))
        return proponer(**(args|otros))

    def aprobar(self,p,**otros):
        return aprobar(**(dict(propuesta_id=p.pk,usuario=self.abogado,operacion=uuid4(),confirmado=True,fundamento='FUNDAMENTO FICTICIO')|otros))

    def instancia(self):
        self.contrato.fecha_asignacion=date(2026,1,12)
        self.contrato.save()
        return iniciar_seguimiento(contrato_id=self.contrato.pk,usuario=self.abogado,sin_actuaciones_previas=True)

    def calculo(self, verificar=True):
        for y in (2025,2027):
            if not vigente(y): aprobar_ficticio(self.abogado,y)
        self.contrato.fecha_terminacion=date(2025,2,12)
        self.contrato.save()
        c=registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=None,
            datos={'sujeto':'si','regimen':'art11_cpaca','origen':'ejecucion','pacto':'no','liquidacion':'ninguna','incidencias':'no'})
        return self.verificar(c) if verificar else c

    def verificar(self,c):
        return registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=c.pk,
                                  accion='verificar',fundamento='REVISIÓN JURÍDICA FICTICIA')

    def confirmar(self,e,**otros):
        return confirmar_vencimiento(**(dict(evaluacion_id=e.pk,usuario=self.abogado,motivo='CONFIRMACIÓN FICTICIA documentada',
                                            confirmado=True,operacion=uuid4())|otros))

    def test_preparacion_pura_no_aprueba_ni_escribe(self):
        antes=CalendarioVerificado.objects.count()
        p=preparar_nacional(2026)
        self.assertEqual(CalendarioVerificado.objects.count(),antes)
        self.assertTrue(p['advertencias'])
        self.assertEqual(p['politica']['dias_laborables'],[0,1,2,3,4])

    def test_fijos_2026_oraculo(self):
        self.assertTrue({date(2026,1,1),date(2026,5,1),date(2026,7,20),date(2026,8,7),date(2026,12,8),date(2026,12,25)} <= calendario_nacional(2026)[0])

    def test_traslados_lunes_y_coincidencias(self):
        f=calendario_nacional(2026)[0]
        self.assertTrue({date(2026,1,12),date(2026,3,23),date(2026,6,29),date(2026,8,17),date(2026,10,12),date(2026,11,2),date(2026,11,16)} <= f)
        self.assertNotIn(date(2026,1,6),f)
        self.assertNotIn(date(2026,8,15),f)

    def test_pascua_y_festivos_moviles_oraculo_2026(self):
        self.assertTrue({date(2026,4,2),date(2026,4,3),date(2026,5,18),date(2026,6,8),date(2026,6,15)} <= calendario_nacional(2026)[0])

    def test_ley2578_temporalidad_y_descanso(self):
        p=preparar_nacional(2026)
        item=[i for i in p['fundamentos'] if i['norma'].startswith('Ley 2578')][0]
        self.assertEqual((item['conmemoracion'],item['descanso'],item['vigencia_desde']),('2026-07-09','2026-07-13','2026-06-02'))
        self.assertNotIn(date(2025,7,14),calendario_nacional(2025)[0])
        self.assertFalse(any(i['norma'].startswith('Ley 2578') for i in preparar_nacional(2025)['fundamentos']))

    def test_ley2578_lunes_sin_doble_traslado(self):
        self.assertIn(date(2029,7,9),calendario_nacional(2029)[0])
        self.assertNotIn(date(2029,7,16),calendario_nacional(2029)[0])

    def test_bisiesto_y_fijos_fin_de_semana(self):
        self.assertEqual(calcular_vencimiento(date(2024,2,28),1)[0],date(2024,2,29))
        self.assertIn(date(2027,5,1),calendario_nacional(2027)[0])
        self.assertNotIn(date(2027,5,3),calendario_nacional(2027)[0])

    def test_rango_anual_soportado(self):
        with self.assertRaises(ValueError): preparar_nacional(1983)

    def test_propuesta_no_sustituye_aprobada(self):
        a=aprobar_ficticio(self.abogado,2026)
        p=self.propuesta(excepciones=[excepcion('2026-01-13')])
        self.assertEqual(vigente(2026),a)
        self.assertNotIn(date(2026,1,13),calendario_disponible(2026)[0])
        self.assertEqual(estado_vigencia(p),'PENDIENTE_VALIDACION')

    def test_responsable_fuente_y_confirmacion_obligatorios(self):
        with self.assertRaises(ValidationError): self.propuesta(fuente='http://example.invalid')
        with self.assertRaises(ValidationError): self.propuesta(motivo=' ')
        p=self.propuesta()
        with self.assertRaises(ValidationError): self.aprobar(p,confirmado=False)
        with self.assertRaises(ValidationError): self.aprobar(p,fundamento=' ')
        a=self.aprobar(p)
        self.assertEqual(a.responsable,self.abogado)
        self.assertIsNotNone(a.aprobado_en)

    def test_sin_autorizacion_no_aprueba(self):
        with self.assertRaises(PermissionDenied): self.propuesta(usuario=self.otro)
        p=self.propuesta()
        with self.assertRaises(PermissionDenied): self.aprobar(p,usuario=self.otro)

    def test_historia_aprobada_inmutable(self):
        a=aprobar_ficticio(self.abogado,2026)
        with self.assertRaises(ValidationError): a.save()
        with self.assertRaises(ValidationError): a.delete()
        with self.assertRaises(ValidationError): CalendarioVerificado.objects.filter(pk=a.pk).update(festivos=[])
        with self.assertRaises(ValidationError): CalendarioVerificado.objects.bulk_create([a])

    def test_no_se_puede_aprobar_fuera_del_servicio(self):
        with self.assertRaises(ValidationError): CalendarioVerificado.objects.create(anio=2026,fuente='https://example.invalid',festivos=[],estado='aprobado')

    def test_versiones_reemplazadas_sin_mutacion(self):
        a=aprobar_ficticio(self.abogado,2026)
        antes=deepcopy(a.festivos)
        b=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')])
        a.refresh_from_db()
        self.assertEqual(a.festivos,antes)
        self.assertEqual(a.estado,'aprobado')
        self.assertEqual(estado_vigencia(a),'REEMPLAZADO')
        self.assertEqual(b.anterior,a)
        self.assertGreater(b.version,a.version)

    def test_comparacion_determinista_sin_escrituras(self):
        a=aprobar_ficticio(self.abogado,2026)
        p=self.propuesta(excepciones=[excepcion('2026-01-13')])
        antes=Auditoria.objects.count()
        d=comparar(a,p)
        self.assertEqual(d,comparar(a,p))
        self.assertEqual(d['clasificacion'],[{'fecha':'2026-01-13','antes':True,'despues':False}])
        self.assertEqual(Auditoria.objects.count(),antes)

    def test_excepcion_retirada_rehabilita_fecha(self):
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')])
        b=aprobar_ficticio(self.abogado,2026)
        self.assertEqual(comparar(a,b)['clasificacion'],[{'fecha':'2026-01-13','antes':False,'despues':True}])

    def test_sabado_habil_excepcional_con_fuente(self):
        aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-17',True)])
        dias,_=calendario_disponible(2026)
        self.assertTrue(es_dia_habil(date(2026,1,17),festivos_verificados=dias))
        self.assertFalse(es_dia_habil(date(2026,1,24),festivos_verificados=dias))
        self.assertEqual(calcular_vencimiento(date(2026,1,16),1,proveedor=calendario_disponible)[0],date(2026,1,17))

    def test_excepciones_invalidas_y_feriado_no_derogable(self):
        for items in ([excepcion('2025-01-13')],[excepcion('2026-01-13'),excepcion('2026-01-13')],
                      [excepcion('2026-01-01',True)],[{'fecha':'2026-01-13'}]):
            with self.subTest(items=items),self.assertRaises(ValidationError): self.propuesta(excepciones=items)

    def test_politica_no_inventada(self):
        with self.assertRaises(ValidationError): self.propuesta(politica={'dias_laborables':[0,1,2,3,4,5]})

    def test_reintentos_idempotentes_y_conflictos(self):
        op=uuid4();p=self.propuesta(operacion=op)
        self.assertEqual(self.propuesta(operacion=op).pk,p.pk)
        with self.assertRaises(ValidationError): self.propuesta(operacion=op,motivo='DIFERENTE')
        op=uuid4();a=self.aprobar(p,operacion=op)
        self.assertEqual(self.aprobar(p,operacion=op).pk,a.pk)

    def test_propuesta_obsoleta_no_puede_aprobar(self):
        a=self.propuesta();b=self.propuesta()
        self.aprobar(a)
        with self.assertRaises(ValidationError): self.aprobar(b)
        with self.assertRaises(ValidationError): self.propuesta(anterior_id=None)

    def test_aprobacion_revierte_completa_ante_error_auditoria(self):
        p=self.propuesta()
        with patch.object(Auditoria.objects,'create',side_effect=RuntimeError('FALLO FICTICIO')):
            with self.assertRaises(RuntimeError): self.aprobar(p)
        self.assertIsNone(vigente(2026))

    def test_fallo_durante_impacto_revierte_version_y_evaluaciones(self):
        a=aprobar_ficticio(self.abogado,2026);i=self.instancia()
        p=self.propuesta(excepciones=[excepcion('2026-01-13')])
        original=Auditoria.objects.create
        def auditar(**datos):
            if datos['accion']=='evaluar_impacto_calendario':raise RuntimeError('FALLO DE IMPACTO FICTICIO')
            return original(**datos)
        with patch.object(Auditoria.objects,'create',side_effect=auditar):
            with self.assertRaises(RuntimeError):self.aprobar(p)
        self.assertEqual(vigente(2026),a)
        self.assertFalse(EvaluacionCalendario.objects.filter(instancia=i).exists())

    def test_plazos15_8_5_oraculo_festivo_intermedio(self):
        aprobar_ficticio(self.abogado,2026)
        for n,esperada in [(15,date(2026,8,4)),(8,date(2026,7,24)),(5,date(2026,7,21))]:
            with self.subTest(n=n):
                f,p=calcular_vencimiento(date(2026,7,10),n,proveedor=calendario_disponible)
                self.assertEqual(f,esperada)
                self.assertTrue(all(v['aprobado'] for v in p.values()))

    def test_cruce_diciembre_enero_cobertura_faltante(self):
        aprobar_ficticio(self.abogado,2026)
        f,p=calcular_vencimiento(date(2026,12,30),2,proveedor=calendario_disponible)
        self.assertEqual(f,date(2027,1,4))
        self.assertTrue(p['2026']['aprobado']);self.assertFalse(p['2027']['aprobado'])
        self.assertIsNone(p['2027']['fechas'][0]['version_id'])

    def test_cruce_diciembre_enero_aprobado(self):
        a=aprobar_ficticio(self.abogado,2026);b=aprobar_ficticio(self.abogado,2027)
        _,p=calcular_vencimiento(date(2026,12,30),2,proveedor=calendario_disponible)
        self.assertEqual(p['2026']['fechas'][0]['version_id'],a.pk)
        self.assertEqual(p['2027']['fechas'][0]['version_id'],b.pk)

    def test_instancia_conserva_dependencias_y_fecha_original(self):
        aprobar_ficticio(self.abogado,2026)
        i=self.instancia();original=i.fecha_vencimiento
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')])
        i.refresh_from_db();e=a.evaluaciones.get(instancia=i)
        self.assertEqual(e.clasificacion,'afectado')
        self.assertEqual(i.fecha_vencimiento,original)
        self.assertEqual(date.fromisoformat(e.detalle['teorico']),date(2026,2,3))
        self.assertTrue(i.procedencia_calendario['2026']['fechas'])

    def test_confirmacion_abierta_auditada_sin_sobrescritura(self):
        aprobar_ficticio(self.abogado,2026)
        i=self.instancia();original=i.fecha_vencimiento
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')]);e=a.evaluaciones.get(instancia=i)
        with self.assertRaises(ValidationError): self.confirmar(e,confirmado=False)
        with self.assertRaises(ValidationError): self.confirmar(e,motivo=' ')
        c=self.confirmar(e);i.refresh_from_db()
        self.assertEqual(i.fecha_vencimiento,original)
        self.assertEqual(revisar_instancia(i)['vigente'],'2026-02-03')
        self.assertEqual(c.anterior,e)
        self.assertTrue(Auditoria.objects.filter(accion='confirmar_vencimiento',identificador_registro=str(c.pk)).exists())
        self.contrato.refresh_from_db();self.assertEqual(self.contrato.estado_actual,self.estado)

    def test_confirmacion_idempotente(self):
        i=self.instancia();a=aprobar_ficticio(self.abogado,2026);e=a.evaluaciones.get(instancia=i)
        op=uuid4();c=self.confirmar(e,operacion=op)
        self.assertEqual(self.confirmar(e,operacion=op).pk,c.pk)

    def test_instancia_finalizada_no_rectificable(self):
        aprobar_ficticio(self.abogado,2026)
        i=self.instancia()
        models.QuerySet.update(InstanciaSeguimiento.objects.filter(pk=i.pk),fecha_finalizacion=date(2026,2,4))
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')]);e=a.evaluaciones.get(instancia=i)
        self.assertTrue(e.detalle['cerrada'])
        with self.assertRaises(ValidationError): self.confirmar(e)
        i.refresh_from_db();self.assertEqual(i.fecha_vencimiento,date(2026,2,2))

    def test_cambio_fuera_de_intervalo_no_afecta_instancia(self):
        aprobar_ficticio(self.abogado,2026);i=self.instancia()
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-12-30')])
        self.assertEqual(a.evaluaciones.get(instancia=i).clasificacion,'sin_impacto')

    def test_legado_no_se_convierte_en_aprobacion(self):
        CalendarioVerificado.objects.create(anio=2026,fuente='https://example.invalid',festivos=preparar_nacional(2026)['festivos'])
        self.assertIn('PROVISIONAL',calendario_disponible(2026)[1])
        i=self.instancia()
        self.assertEqual(revisar_instancia(i)['clasificacion'],'incierto')

    def test_documento_operativo_provisional_visible(self):
        i=self.instancia()
        texto,detalle=vencimiento_documental(i,i.fecha_vencimiento)
        self.assertIn('PROVISIONAL',texto);self.assertTrue(detalle['provisional'])

    def test_documento_operativo_afectado_bloqueado_hasta_confirmar(self):
        aprobar_ficticio(self.abogado,2026);i=self.instancia()
        a=aprobar_ficticio(self.abogado,2026,[excepcion('2026-01-13')])
        with self.assertRaises(ValidationError): vencimiento_documental(i,date(2026,2,3))
        self.confirmar(a.evaluaciones.get(instancia=i))
        self.assertEqual(vencimiento_documental(i,date(2026,2,3))[0],'2026-02-03')

    def test_calculo_verificado_dependencias_pertinentes(self):
        c=self.calculo()
        self.assertEqual(fecha_confirmada(self.contrato),date(2027,8,12))
        self.assertTrue(c.resultado['dependencias_calendario']['fechas'])
        self.assertTrue(all(d['aprobado'] for d in c.resultado['dependencias_calendario']['fechas']))

    def test_h04_cambio_relevante_bloquea_nuevos_usos(self):
        c=self.calculo();snapshot=deepcopy(c.resultado)
        a=aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        self.assertIsNone(fecha_confirmada(self.contrato))
        self.assertEqual(a.evaluaciones.get(calculo=c).clasificacion,'afectado')
        c.refresh_from_db();self.assertEqual(c.resultado,snapshot);self.assertEqual(c.estado,Estado.VERIFICADO)
        self.contrato.refresh_from_db();self.assertEqual(self.contrato.estado_actual,self.estado)

    def test_h04_propuesta_rectificacion_y_nueva_verificacion(self):
        c=self.calculo()
        aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        p=registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=c.pk,datos=c.insumos)
        self.assertEqual(p.estado,Estado.REVISION)
        self.assertIsNone(p.resultado['limite_competencia'])
        rect={k:p.resultado[k] for k in FECHAS_RESULTADO};rect['limite_competencia']='2027-08-13'
        r=registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=p.pk,
            accion='rectificar',rectificacion=rect,motivo='REVISIÓN FICTICIA DE INHABILIDAD',fundamento='TRASLADO MANUAL FICTICIO REVISADO')
        n=self.verificar(r)
        self.assertEqual(n.anterior,r);self.assertEqual(fecha_confirmada(self.contrato),date(2027,8,13))
        self.assertEqual(c.resultado['limite_competencia'],'2027-08-12')

    def test_cambio_irrelevante_conserva_verificacion_con_evidencia(self):
        c=self.calculo()
        a=aprobar_ficticio(self.abogado,2027,[excepcion('2027-12-30')])
        e=a.evaluaciones.get(calculo=c)
        self.assertEqual(e.clasificacion,'sin_impacto');self.assertTrue(e.detalle['comprobaciones'])
        self.assertEqual(fecha_confirmada(self.contrato),date(2027,8,12))

    def test_borrador_no_invalida_verificacion_aprobada(self):
        self.calculo();self.propuesta(2027,excepciones=[excepcion('2027-08-12')])
        self.assertEqual(fecha_confirmada(self.contrato),date(2027,8,12))

    def test_restitucion_calendario_no_rehabilita_calculo_sin_nueva_revision(self):
        c=self.calculo()
        aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        aprobar_ficticio(self.abogado,2027)
        self.assertIsNone(fecha_confirmada(self.contrato))
        p=registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=c.pk,datos=c.insumos)
        self.verificar(p)
        self.assertEqual(fecha_confirmada(self.contrato),date(2027,8,12))

    def test_dependencia_incierta_no_equivale_a_vigencia(self):
        c=self.calculo();resultado=deepcopy(c.resultado);resultado.pop('dependencias_calendario')
        self.assertEqual(evaluar_dependencias(resultado)['clasificacion'],'incierto')
        resultado=deepcopy(c.resultado);resultado['dependencias_calendario']['fechas'][0]['aprobado']=False
        self.assertEqual(evaluar_dependencias(resultado)['clasificacion'],'incierto')

    def test_no_dependiente_explicito_conserva_vigencia(self):
        _,r=evaluar({'sujeto':'no'})
        self.assertEqual(evaluar_dependencias(r)['clasificacion'],'sin_impacto')
        self.assertIsNone(r['limite_competencia'])

    def test_no_verificar_con_anios_no_aprobados(self):
        self.contrato.fecha_terminacion=date(2025,2,12);self.contrato.save()
        c=registrar_calculo(contrato_id=self.contrato.pk,usuario=self.abogado,operacion=uuid4(),anterior_id=None,
            datos={'sujeto':'si','regimen':'art11_cpaca','origen':'ejecucion','pacto':'no','liquidacion':'ninguna','incidencias':'no'})
        self.assertTrue(c.resultado['advertencias_calendario'])
        with self.assertRaises(ValidationError): self.verificar(c)

    def test_verificacion_obsoleta_entre_propuesta_y_revision_rechazada(self):
        c=self.calculo(verificar=False)
        aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        with self.assertRaises(ValidationError): self.verificar(c)

    def test_meses_anios_nominales_no_se_trasladan_automaticamente(self):
        self.assertEqual(sumar_meses(date(2024,1,31),1),date(2024,2,29))
        self.assertEqual(sumar_anios(date(2024,2,29),1),date(2025,2,28))
        _,r=evaluar({'sujeto':'si','regimen':'art11_cpaca','origen':'ejecucion','fecha_terminacion':'2025-02-10',
                     'pacto':'no','liquidacion':'ninguna','incidencias':'no'},calendario=calendario_nacional)
        self.assertEqual(r['fin_unilateral'],'2025-08-10')
        self.assertTrue(r['advertencias']);self.assertIsNone(r['limite_competencia'])
        self.assertIn('no determina vacancia judicial',r['dependencias_calendario']['alcance'])

    def test_regimen_especial_no_se_resuelve_con_calendario_administrativo(self):
        estado,r=evaluar({'sujeto':'si','regimen':'judicial_especial','origen':'ejecucion','fecha_terminacion':'2025-02-12'})
        self.assertEqual(estado,Estado.REVISION);self.assertIsNone(r['limite_competencia'])

    def test_docx_nuevo_omite_fecha_obsoleta_y_historico_intacto(self):
        c=self.calculo()
        tipo=TipoActa.objects.create(nombre='ACTA FICTICIA G')
        plantilla=registrar_plantilla(usuario=self.abogado,tipo=tipo.clave_documental,tipo_acta=tipo,nombre='FICTICIA',
            archivo=docx('<w:r><w:t>{{fecha_limite_competencia}}</w:t></w:r>'),activar=True,ficticia=True)
        def generar(): return generar_requerimiento(usuario=self.abogado,contrato_id=self.contrato.pk,tipo='',tipo_acta_id=tipo.pk,plantilla_id=plantilla.pk,operacion=uuid4())
        g=generar();viejo=g.documento.archivo.read();g.documento.archivo.close()
        aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        n=generar()
        self.assertIsNone(n.calculo_competencia)
        self.assertIsNone(n.datos_utilizados.get('fecha_limite_competencia'))
        self.assertIn('fecha_limite_competencia',n.campos_pendientes)
        g.refresh_from_db();self.assertEqual(g.calculo_competencia,c)
        self.assertEqual(g.documento.archivo.read(),viejo);g.documento.archivo.close()

    def test_revision_manual_no_republica_verificacion_obsoleta(self):
        self.calculo()
        tipo=TipoActa.objects.create(nombre='ACTA FICTICIA PARA REVISIÓN G')
        plantilla=registrar_plantilla(usuario=self.abogado,tipo=tipo.clave_documental,tipo_acta=tipo,nombre='FICTICIA',
            archivo=docx('<w:r><w:t>{{fecha_limite_competencia}}</w:t></w:r>'),activar=True,ficticia=True)
        args=dict(usuario=self.abogado,contrato_id=self.contrato.pk,tipo='',tipo_acta_id=tipo.pk,plantilla_id=plantilla.pk)
        g=generar_requerimiento(**args,operacion=uuid4())
        aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        with self.assertRaises(ValidationError):
            generar_requerimiento(**args,operacion=uuid4(),anterior_id=g.pk,archivo_revision=docx(),motivo='CORRECCIÓN FICTICIA')
        self.assertEqual(DocumentoGenerado.objects.filter(contrato=self.contrato).count(),1)

    def test_reintento_con_otras_excepciones_rechazado(self):
        op=uuid4();self.propuesta(operacion=op)
        with self.assertRaises(ValidationError):self.propuesta(operacion=op,excepciones=[excepcion('2026-01-13')])

    def test_admin_excepcion_malformada_retorna_error_del_formulario(self):
        self.client.force_login(self.abogado)
        r=self.client.post(reverse('admin:sigelc_calendario_preparar'),{'anio':2026,'fuente':'https://example.invalid/FICTICIO',
            'motivo':'FICTICIO','operacion':uuid4(),'excepciones':'{"fecha":"2026-01-13"}'})
        self.assertEqual(r.status_code,200)
        self.assertContains(r,'Las excepciones deben ser una lista')

    def test_evaluacion_inmutable(self):
        self.calculo();a=aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        e=a.evaluaciones.first()
        with self.assertRaises(ValidationError): e.save()
        with self.assertRaises(ValidationError): e.delete()
        with self.assertRaises(ValidationError): EvaluacionCalendario.objects.filter(pk=e.pk).update(motivo='OTRO')

    def test_admin_consulta_compara_y_aprueba(self):
        self.client.force_login(self.abogado)
        url=reverse('admin:sigelc_calendario_preparar')
        self.assertContains(self.client.get(url),'Preparar calendario')
        r=self.client.post(url,{'anio':2026,'fuente':'https://example.invalid/FICTICIO','motivo':'FICTICIO','operacion':uuid4(),'excepciones':'[]'})
        self.assertEqual(r.status_code,302)
        p=CalendarioVerificado.objects.get(estado='propuesta')
        url=reverse('admin:sigelc_calendario_revisar',args=[p.pk])
        self.assertContains(self.client.get(url),'PENDIENTE_VALIDACION')
        r=self.client.post(url,{'accion':'aprobar','fundamento':'REVISIÓN FICTICIA','confirmado':'on','operacion':uuid4()})
        self.assertEqual(r.status_code,302)
        self.assertContains(self.client.get(r.url),'APROBADO')

    def test_admin_anonimo_y_no_operativo_sin_acceso(self):
        url=reverse('admin:sigelc_calendario_preparar')
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.get(url).status_code,403)

    def test_admin_post_sin_csrf_no_escribe(self):
        from django.test import Client
        cliente=Client(enforce_csrf_checks=True)
        cliente.force_login(self.abogado)
        antes=CalendarioVerificado.objects.count()
        r=cliente.post(reverse('admin:sigelc_calendario_preparar'),{'anio':2026,'fuente':'https://example.invalid/FICTICIO',
            'motivo':'FICTICIO','operacion':uuid4(),'excepciones':'[]'})
        self.assertEqual(r.status_code,403)
        self.assertEqual(CalendarioVerificado.objects.count(),antes)

    def test_admin_calculo_muestra_razon_vigencia(self):
        self.calculo();aprobar_ficticio(self.abogado,2027,[excepcion('2027-08-12')])
        self.client.force_login(self.abogado)
        self.assertContains(self.client.get(reverse('admin:gestion_contractual_competencia',args=[self.contrato.pk])),'Vigencia de calendario: afectado')
