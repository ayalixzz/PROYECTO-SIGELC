import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Aviso, Carga, Icon, RecursoError, SinDatos } from '../components/Ui'
import { api, ErrorApi, mensaje, useRecurso } from '../services/api'
import type { AlertaContractual, AlertasExpediente, AlertasPanel, AvisoRevisionContractual, UltimoMemorando } from '../services/api'
import { fecha, hoyBogota } from '../services/formato'

const prioridades: Record<string, string> = { critica: 'Crítica', alta: 'Alta', media: 'Media', baja: 'Baja', normal: 'Normal', ninguna: 'Sin prioridad' }
const fuentes: Record<string, string> = {
  calculo_competencia: 'Cálculo jurídico verificado',
  competencia: 'Cálculo jurídico verificado',
  instancia_seguimiento: 'Instancia de seguimiento',
  instancia: 'Instancia de seguimiento',
}
const origenes: Record<string, string> = {
  competencia: 'Cálculo jurídico verificado', operativa: 'Seguimiento operativo',
  tarea: 'Tarea', manual: 'Decisión del abogado', automatica: 'Seguimiento del expediente',
}

function tituloPrioridad(nivel: string) {
  return prioridades[nivel] || (nivel ? nivel.replaceAll('_', ' ') : 'Sin prioridad')
}

function Prioridad({ nivel }: { nivel: string }) {
  const tono = ['critica', 'alta'].includes(nivel) ? 'amber' : nivel === 'media' ? 'blue' : 'slate'
  return <span className={`badge badge-${tono}`}>{tituloPrioridad(nivel)}</span>
}

function plazo(alerta: AlertaContractual) {
  const n = alerta.dias_restantes
  if (n === null) return 'Fecha superada'
  if (n === 0) return 'Fecha prevista para hoy'
  const cantidad = Math.abs(n)
  const unidad = alerta.unidad === 'habiles'
    ? cantidad === 1 ? 'día hábil' : 'días hábiles'
    : cantidad === 1 ? 'día calendario' : 'días calendario'
  return n > 0 ? `En ${cantidad} ${unidad}` : `La fecha pasó hace ${cantidad} ${unidad}`
}

function registrarHitos(path: string, signal: AbortSignal) {
  return api<{ registradas: number; fecha: string }>(path, { method: 'POST', body: {}, signal })
}

type RegistroHitos = { error: string; completado: boolean }
function useRegistroHitos(path: string | null) {
  const [intento, setIntento] = useState(0)
  const [registro, setRegistro] = useState<RegistroHitos>({ error: '', completado: false })
  useEffect(() => {
    if (!path) return
    const abort = new AbortController()
    registrarHitos(path, abort.signal).then(() => {
      if (!abort.signal.aborted) setRegistro({ error: '', completado: true })
    }).catch((error: unknown) => {
      if (!abort.signal.aborted) setRegistro({ error: mensaje(error), completado: false })
    })
    return () => abort.abort()
  }, [path, intento])
  return {
    ...registro, reintentar: () => {
      setRegistro({ error: '', completado: false })
      setIntento((value) => value + 1)
    },
  }
}

function HitosError({ error, reintentar }: { error: string; reintentar: () => void }) {
  return error ? <Aviso tipo="error">No se pudo actualizar el historial de alertas. {error}
    <button type="button" className="text-action ml-3" onClick={reintentar}>Reintentar registro</button>
  </Aviso> : null
}

function enlaceExpediente(id: number) {
  return `#/contratos/${id}?seccion=alertas`
}

function ListaPanel({ titulo, alertas, total, vacio }: {
  titulo: string; alertas: AlertaContractual[]; total: number; vacio: string
}) {
  return <section className="panel dashboard-panel">
    <div className="panel-heading"><span className="heading-icon tint-amber"><Icon name="alert" size={18} /></span>
      <div><h2>{titulo}</h2>{total > 0 && <p>{total.toLocaleString('es-CO')} {total === 1 ? 'alerta vigente' : 'alertas vigentes'}</p>}</div>
    </div>
    {alertas.length ? <ul className="dashboard-list">{alertas.map((item) => <li key={item.clave}>
      <a href={enlaceExpediente(item.contrato_id)} className="dashboard-row">
        <div className="truncate"><strong>{item.numero_contrato} · {item.anio_contractual}</strong>
          <small>{item.titulo} · {fecha(item.fecha_objetivo)}</small></div>
        <Prioridad nivel={item.prioridad} /><Icon name="chevron" size={16} />
      </a>
    </li>)}</ul> : <SinDatos icon="alert" title={vacio} />}
  </section>
}

function Avisos({ avisos }: { avisos: AvisoRevisionContractual[] }) {
  return <ul className="dashboard-list">
    {avisos.map((item) => <li key={`${item.contrato_id}:${item.tipo}`}>
      <a href={enlaceExpediente(item.contrato_id)} className="dashboard-row">
        <div className="truncate"><strong>{item.numero_contrato} · {item.anio_contractual}</strong><small>{item.motivo}</small></div>
        <Icon name="chevron" size={16} />
      </a>
    </li>)}
  </ul>
}

export function ResumenAlertas({ datos, puedeRegistrar }: { datos: AlertasPanel; puedeRegistrar: boolean }) {
  const registro = useRegistroHitos(puedeRegistrar ? 'alertas/registrar/' : null)
  return <div className="ri-stack mb-4">
    <HitosError error={registro.error} reintentar={registro.reintentar} />
    <div className="dashboard-grid">
      <ListaPanel titulo="Alertas de pérdida de competencia" alertas={datos.juridicas} total={datos.totales.juridicas} vacio="Sin alertas jurídicas vigentes" />
      <ListaPanel titulo="Alertas operativas" alertas={datos.operativas} total={datos.totales.operativas} vacio="Sin alertas operativas vigentes" />
      {datos.requieren_revision.length > 0 && <section className="panel dashboard-panel">
        <div className="panel-heading"><span className="heading-icon tint-amber"><Icon name="clock" size={18} /></span>
          <div><h2>Vencimientos por verificar</h2><p>{datos.totales.requieren_revision.toLocaleString('es-CO')} por revisar</p></div>
        </div>
        <Avisos avisos={datos.requieren_revision} />
      </section>}
      {datos.prioridades_temporales.length > 0 && <section className="panel dashboard-panel">
        <div className="panel-heading"><span className="heading-icon tint-amber"><Icon name="alert" size={18} /></span>
          <div><h2>Prioridades temporales</h2></div>
        </div>
        <ul className="dashboard-list">{datos.prioridades_temporales.map((item) => <li key={item.contrato_id}>
          <a href={enlaceExpediente(item.contrato_id)} className="dashboard-row">
            <div className="truncate"><strong>{item.numero_contrato} · {item.anio_contractual}</strong>
              <small>Hasta {fecha(item.vence)} · {item.motivo}</small></div>
            <Prioridad nivel="alta" /><Icon name="chevron" size={16} />
          </a>
        </li>)}</ul>
      </section>}
    </div>
  </div>
}

export function ResumenMemorando({ memo, contratoId }: { memo: UltimoMemorando | null; contratoId?: number }) {
  return <section className="panel detail-panel">
    <div className="section-head">
      <div><h2>Último memorando</h2></div>
      {memo && contratoId && <a href={`#/contratos/${contratoId}?seccion=actuaciones`} className="text-action">Ver actuaciones</a>}
    </div>
    {memo ? <dl className="definition-grid">
      <div><dt>Radicado</dt><dd>{memo.radicado}</dd></div>
      <div><dt>Fecha</dt><dd>{fecha(memo.fecha)}</dd></div>
      <div className="col-span-2"><dt>Asunto</dt><dd>{memo.titulo}</dd></div>
    </dl> : <p className="subtle">Aún no hay un memorando registrado.</p>}
  </section>
}

function DatosAlerta({ item }: { item: AlertaContractual }) {
  const fuente = fuentes[item.fuente.tipo] || item.fuente.tipo.replaceAll('_', ' ')
  return <>
    <div className="ri-inline"><Prioridad nivel={item.prioridad} />
      <strong>{item.titulo}</strong>
    </div>
    <p>{fecha(item.fecha_objetivo)} · {plazo(item)}</p>
    <p className="subtle">{fuente} #{item.fuente.id}{item.fuente.version ? ` · versión ${item.fuente.version}` : ''}</p>
  </>
}

function HistorialAlertas({ historial }: { historial: AlertasExpediente['historial'] }) {
  return <section className="panel detail-panel">
    <h2>Historial de alertas</h2>
    {historial.length ? <ul className="ri-stack mt-3">{historial.map((item) => <li className="ri-citation" key={item.id ?? item.clave}>
      <div className="ri-inline"><strong>{item.titulo}</strong>
        <span className={`badge badge-${item.activa ? 'blue' : 'slate'}`}>{item.activa ? 'Vigente' : 'Histórica'}</span>
      </div>
      <p className="subtle">Fecha objetivo {fecha(item.fecha_objetivo)} · registrada {fecha(item.registrada_en)}</p>
    </li>)}</ul> : <p className="subtle mt-2">Aún no se han registrado hitos de alerta.</p>}
  </section>
}

function PrioridadTemporal({ id, guardar }: { id: number; guardar: (prioridad: AlertasExpediente['prioridad']) => void }) {
  const [busy, setBusy] = useState(false)
  const [aviso, setAviso] = useState<{ tipo: 'exito' | 'error'; mensaje: string }>()
  const solicitud = useRef<{ huella: string; operacion: string } | null>(null)
  async function registrar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    const motivo = String(data.get('motivo') ?? '').trim()
    const vence = String(data.get('vence') ?? '')
    if (!motivo) return setAviso({ tipo: 'error', mensaje: 'Indica el motivo para priorizar el expediente.' })
    const huella = JSON.stringify([motivo, vence])
    const operacion = solicitud.current?.huella === huella ? solicitud.current.operacion : crypto.randomUUID()
    solicitud.current = { huella, operacion }
    setBusy(true)
    setAviso(undefined)
    try {
      const actualizado = await api<AlertasExpediente>(`contratos/${id}/alertas/prioridad/`, {
        method: 'POST', body: { operacion, motivo, vence },
      })
      guardar(actualizado.prioridad)
      solicitud.current = null
      form.reset()
      setAviso({ tipo: 'exito', mensaje: 'Prioridad temporal registrada.' })
    } catch (error) {
      if (error instanceof ErrorApi && error.status >= 400 && error.status < 500) solicitud.current = null
      const campos = error instanceof ErrorApi ? Object.values(error.campos).join(' ') : ''
      setAviso({ tipo: 'error', mensaje: campos || mensaje(error) })
    } finally { setBusy(false) }
  }
  return <details className="ri-card">
    <summary><Icon name="plus" size={17} /> Marcar prioridad alta temporal</summary>
    <div className="ri-card-body">
      <p className="subtle">La prioridad crítica de competencia prevalece mientras esté vigente.</p>
      {aviso && <Aviso tipo={aviso.tipo}>{aviso.mensaje}</Aviso>}
      <form onSubmit={registrar}>
        <fieldset className="ri-fields-form" disabled={busy}>
          <label className="field"><span>Motivo</span><textarea name="motivo" rows={3} maxLength={1000} required /></label>
          <label className="field"><span>Vigente hasta</span><input type="date" name="vence" min={hoyBogota()} required /></label>
          <button type="submit" className="button button-secondary button-small">
            <Icon name="check" size={17} />{busy ? 'Guardando…' : 'Guardar prioridad temporal'}
          </button>
        </fieldset>
      </form>
    </div>
  </details>
}

export function AlertasContrato({ id }: { id: number }) {
  const path = `contratos/${id}/alertas/`
  const recurso = useRecurso<AlertasExpediente>(path)
  const [prioridadActualizada, setPrioridadActualizada] = useState<AlertasExpediente['prioridad']>()
  const { data } = recurso
  const registro = useRegistroHitos(data?.puede_registrar ? `${path}registrar/` : null)
  // Update just the history after registration without refetching indefinitely.
  const registroSolicitado = registro.completado
  const [historial, setHistorial] = useState<AlertasExpediente['historial']>()
  useEffect(() => {
    if (!registroSolicitado) return
    const abort = new AbortController()
    api<AlertasExpediente>(path, { signal: abort.signal }).then((ficha) => {
      if (!abort.signal.aborted) setHistorial(ficha.historial)
    }).catch(() => { /* Conserva el historial de la consulta inicial. */ })
    return () => abort.abort()
  }, [path, registroSolicitado])

  if (recurso.loading) return <Carga texto="Consultando alertas y prioridad…" />
  if (recurso.error) return <RecursoError error={recurso.error} recargar={recurso.recargar} />
  if (!data) return null
  const prioridad = prioridadActualizada ?? data.prioridad
  const actual = historial ?? data.historial
  return <div className="ri-stack">
    <HitosError error={registro.error} reintentar={registro.reintentar} />
    <div className="dashboard-grid">
      <section className="panel detail-panel">
        <div className="section-head"><h2>Prioridad de seguimiento</h2><Prioridad nivel={prioridad.nivel} /></div>
        {prioridad.motivo && <p>{prioridad.motivo}</p>}
        {prioridad.origen && prioridad.origen !== 'ninguno' && <p className="subtle">Origen · {origenes[prioridad.origen] || prioridad.origen.replaceAll('_', ' ')}</p>}
        {prioridad.vence && <p className="subtle">Vigente hasta {fecha(prioridad.vence)}</p>}
        {data.fecha_competencia && <dl className="definition-grid mt-3">
          <div><dt>Fecha límite de competencia</dt><dd>{fecha(data.fecha_competencia.fecha)}</dd></div>
          <div><dt>Fuente</dt><dd>Cálculo verificado · versión {data.fecha_competencia.fuente.version}</dd></div>
        </dl>}
        {data.puede_registrar && <PrioridadTemporal id={id} guardar={setPrioridadActualizada} />}
      </section>
      <ResumenMemorando memo={data.ultimo_memorando} contratoId={id} />
    </div>
    <section className="panel detail-panel">
      <div className="section-head"><div><h2>Alertas vigentes</h2><p className="subtle">Seguimiento de fechas sustentadas en el expediente.</p></div></div>
      {data.alertas.length ? <ul className="ri-stack">{data.alertas.map((item) => <li className="ri-citation" key={item.clave}><DatosAlerta item={item} /></li>)}</ul>
        : <p className="subtle">No hay alertas vigentes en este expediente.</p>}
    </section>
    {data.revisiones.length > 0 && <Aviso><strong>Fechas por verificar</strong><ul>
      {data.revisiones.map((item) => <li key={item.tipo}>{item.motivo}</li>)}
    </ul></Aviso>}
    <HistorialAlertas historial={actual} />
  </div>
}
