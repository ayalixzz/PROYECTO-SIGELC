import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Aviso, Carga, Icon, RecursoError, SinDatos } from '../components/Ui'
import { api, ErrorApi, mensaje, useRecurso } from '../services/api'
import type { Documento, Expediente, UltimoMemorando } from '../services/api'
import { fecha, hoyBogota } from '../services/formato'
import './revision-inteligente.css'
import { ResumenMemorando } from './Alertas'

type Operacion = 'planificar' | 'memorando' | 'realizar' | 'cancelar'
type Estado = 'pendiente' | 'realizada' | 'cancelada' | 'registrado'
type EventoActuacion = {
  id: number; accion: 'realizar' | 'cancelar'; fecha: string; descripcion: string
  documento_id: number | null; usuario: string; registrado_en: string
}
type Actuacion = {
  id: number; tipo: 'tarea' | 'memorando'; titulo: string; descripcion: string; fecha: string
  vence: string | null; radicado: string; documento_id: number | null
  estado: Estado; eventos: EventoActuacion[]; usuario: string; registrado_en: string
}
type Actuaciones = { actuaciones: Actuacion[]; puede_registrar: boolean; ultimo_memorando?: UltimoMemorando | null }
type Cambiar = (accion: Operacion, data: FormData, id?: number) => Promise<boolean>
type Filtro = 'todas' | 'pendientes' | 'memorandos'

const etiquetas: Record<Estado, string> = {
  pendiente: 'Pendiente', realizada: 'Realizada', cancelada: 'Cancelada', registrado: 'Registrado',
}

function DocumentoOpcional({ documentos }: { documentos: Documento[] }) {
  if (!documentos.length) return null
  return <label className="field"><span>Documento de soporte</span>
    <select name="documento_id">
      <option value="">Sin documento vinculado</option>
      {documentos.map((doc) => <option value={doc.id} key={doc.id}>{doc.nombre_original}</option>)}
    </select>
  </label>
}

function Soporte({ id, documentos }: { id: number | null; documentos: Documento[] }) {
  if (!id) return null
  const documento = documentos.find((doc) => doc.id === id)
  const url = documento?.descarga_url
  return <p className="subtle">Soporte · {url && /^\/api\/contratos\/\d+\/documentos\/\d+\/descargar\/$/.test(url)
    ? <a href={url} className="text-action">{documento.nombre_original}</a>
    : documento?.nombre_original || 'Documento vinculado al expediente'}</p>
}

function NuevaActuacion({ documentos, guardar, busy }: { documentos: Documento[]; guardar: Cambiar; busy: boolean }) {
  const [accion, setAccion] = useState<'planificar' | 'memorando'>('planificar')
  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    if (await guardar(accion, new FormData(form))) form.reset()
  }
  return <section className="panel detail-panel">
    <div className="section-head"><div><h2>Registrar actuación</h2>
      <p className="subtle">Planifica el trabajo pendiente o incorpora un memorando ya radicado.</p></div></div>
    <form className="ri-form" onSubmit={enviar} key={accion}>
      <fieldset className="ri-fields-form" disabled={busy}>
        <label className="field"><span>Tipo de actuación</span>
          <select value={accion} onChange={(event) => setAccion(event.target.value === 'memorando' ? 'memorando' : 'planificar')}>
            <option value="planificar">Tarea pendiente</option>
            <option value="memorando">Memorando</option>
          </select>
        </label>
        <label className="field"><span>{accion === 'memorando' ? 'Asunto del memorando' : 'Qué debes realizar'}</span>
          <input name="titulo" required maxLength={250} />
        </label>
        {accion === 'memorando' ? <>
          <label className="field"><span>Número de radicado</span><input name="radicado" required maxLength={120} /></label>
          <label className="field"><span>Fecha del memorando</span><input name="fecha" type="date" required max={hoyBogota()} /></label>
        </> : <label className="field"><span>Vencimiento de la tarea</span><input name="vence" type="date" /></label>}
        <label className="field"><span>Descripción</span><textarea name="descripcion" rows={3} maxLength={4000} /></label>
        <DocumentoOpcional documentos={documentos} />
        <button className="button button-primary" type="submit"><Icon name="plus" size={17} />
          {accion === 'memorando' ? 'Registrar memorando' : 'Crear tarea'}
        </button>
      </fieldset>
    </form>
  </section>
}

function ResolverTarea({ actuacion, documentos, guardar, busy }: {
  actuacion: Actuacion; documentos: Documento[]; guardar: Cambiar; busy: boolean
}) {
  const [accion, setAccion] = useState<'realizar' | 'cancelar'>('realizar')
  async function resolver(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await guardar(accion, new FormData(event.currentTarget), actuacion.id)
  }
  return <form className="ri-form ri-decision" onSubmit={resolver}>
    <fieldset className="ri-fields-form" disabled={busy}>
      <label className="field"><span>Resultado de la tarea</span>
        <select value={accion} onChange={(event) => setAccion(event.target.value === 'cancelar' ? 'cancelar' : 'realizar')}>
          <option value="realizar">Marcar como realizada</option>
          <option value="cancelar">Cancelar tarea</option>
        </select>
      </label>
      <label className="field"><span>Fecha de la actuación</span>
        <input name="fecha" type="date" required max={hoyBogota()} defaultValue={hoyBogota()} />
      </label>
      <label className="field"><span>{accion === 'cancelar' ? 'Motivo de la cancelación' : 'Acción realizada por el abogado'}</span>
        <textarea name="descripcion" rows={3} required maxLength={4000} />
      </label>
      <DocumentoOpcional documentos={documentos} />
      <button className="button button-secondary button-small" type="submit"><Icon name="check" size={17} /> Guardar resultado de la tarea</button>
    </fieldset>
  </form>
}

function TarjetaActuacion({ actuacion, documentos, editable, busy, guardar }: {
  actuacion: Actuacion; documentos: Documento[]; editable: boolean; busy: boolean; guardar: Cambiar
}) {
  const vencida = actuacion.estado === 'pendiente' && actuacion.vence && actuacion.vence < hoyBogota()
  return <details className="ri-card">
    <summary>
      <span className={`badge badge-${actuacion.estado === 'pendiente' ? 'amber' : 'slate'}`}>{etiquetas[actuacion.estado]}</span>
      <span className="ri-card-title">{actuacion.tipo === 'memorando' ? 'Memorando' : 'Tarea'} · {actuacion.titulo}</span>
      <span className="subtle">{actuacion.vence ? `Vence ${fecha(actuacion.vence)}` : fecha(actuacion.fecha)}</span>
    </summary>
    <div className="ri-card-body">
      {actuacion.radicado && <p><strong>Radicado</strong> · {actuacion.radicado}</p>}
      {vencida && <Aviso>La fecha prevista de esta tarea ya pasó.</Aviso>}
      {actuacion.descripcion && <p className="whitespace-pre-wrap">{actuacion.descripcion}</p>}
      <Soporte id={actuacion.documento_id} documentos={documentos} />
      <p className="subtle">Registro de {actuacion.usuario} · {fecha(actuacion.registrado_en)}</p>
      {actuacion.eventos.length > 0 && <ol className="timeline" aria-label="Historial de la tarea">
        {actuacion.eventos.map((evento) => <li key={evento.id}>
          <span className="timeline-marker"><Icon name="activity" size={16} /></span>
          <div className="timeline-entry">
            <strong>{evento.accion === 'cancelar' ? 'Cancelación' : 'Acción realizada'} · {fecha(evento.fecha)}</strong>
            <p className="whitespace-pre-wrap">{evento.descripcion}</p>
            <Soporte id={evento.documento_id} documentos={documentos} />
            <p className="subtle">Registro de {evento.usuario} · {fecha(evento.registrado_en)}</p>
          </div>
        </li>)}
      </ol>}
      {editable && actuacion.estado === 'pendiente' && <ResolverTarea actuacion={actuacion} documentos={documentos} guardar={guardar} busy={busy} />}
    </div>
  </details>
}

export function ActuacionesPanel({ contrato }: { contrato: Expediente }) {
  const path = `contratos/${contrato.id}/actuaciones/`
  const recurso = useRecurso<Actuaciones>(path)
  const [actual, setActual] = useState<Actuaciones>()
  const [filtro, setFiltro] = useState<Filtro>('todas')
  const [busy, setBusy] = useState(false)
  const [aviso, setAviso] = useState<{ tipo: 'error' | 'exito'; mensaje: string }>()
  const solicitud = useRef<{ huella: string; operacion: string } | undefined>(undefined)
  const data = actual ?? recurso.data

  async function guardar(accion: Operacion, form: FormData, id?: number) {
    const campos: Record<string, string | number> = { accion }
    for (const [nombre, valor] of form.entries()) {
      const dato = String(valor).trim()
      if (dato) campos[nombre] = nombre === 'documento_id' ? Number(dato) : dato
    }
    if (id) campos.actuacion_id = id
    const huella = JSON.stringify(campos)
    const operacion = solicitud.current?.huella === huella ? solicitud.current.operacion : crypto.randomUUID()
    solicitud.current = { huella, operacion }
    setBusy(true)
    setAviso(undefined)
    try {
      const actualizado = await api<Actuaciones>(path, { method: 'POST', body: { ...campos, operacion } })
      setActual(actualizado)
      solicitud.current = undefined
      setAviso({ tipo: 'exito', mensaje: accion === 'planificar' ? 'Tarea registrada.'
        : accion === 'memorando' ? 'Memorando registrado.' : 'Resultado de la tarea registrado.' })
      return true
    } catch (error) {
      if (error instanceof ErrorApi && error.status >= 400 && error.status < 500) solicitud.current = undefined
      const detalle = error instanceof ErrorApi ? Object.values(error.campos).join(' ') : ''
      setAviso({ tipo: 'error', mensaje: detalle || mensaje(error) })
      return false
    } finally { setBusy(false) }
  }

  if (recurso.loading && !data) return <Carga texto="Consultando actuaciones…" />
  if (recurso.error && !data) return <RecursoError error={recurso.error} recargar={recurso.recargar} />
  if (!data) return null

  const actuaciones = data.actuaciones.filter((item) => filtro === 'todas'
    || (filtro === 'pendientes' && item.estado === 'pendiente') || (filtro === 'memorandos' && item.tipo === 'memorando'))
  return <div className="ri-stack">
    {aviso && <Aviso tipo={aviso.tipo}>{aviso.mensaje}{aviso.tipo === 'error' && <button type="button" className="text-action ml-3"
      onClick={() => { setActual(undefined); setAviso(undefined); recurso.recargar() }}>Actualizar actuaciones</button>}</Aviso>}
    {data.ultimo_memorando !== undefined && <ResumenMemorando memo={data.ultimo_memorando} />}
    <section className="panel detail-panel">
      <div className="section-head"><div><h2>Tareas y memorandos</h2>
        <p className="subtle">Consulta los pendientes, las actuaciones realizadas y sus soportes.</p></div>
        <label className="field"><span>Mostrar</span><select value={filtro} onChange={(event) => setFiltro(event.target.value as Filtro)}>
          <option value="todas">Todas las actuaciones</option><option value="pendientes">Tareas pendientes</option><option value="memorandos">Memorandos</option>
        </select></label>
      </div>
      {actuaciones.length ? actuaciones.map((actuacion) => <TarjetaActuacion key={actuacion.id} actuacion={actuacion}
        documentos={contrato.documentos} editable={data.puede_registrar} busy={busy} guardar={guardar} />)
        : <SinDatos title="Sin actuaciones para mostrar" text="Las tareas y los memorandos registrados aparecerán aquí." />}
    </section>
    {data.puede_registrar && <NuevaActuacion documentos={contrato.documentos} guardar={guardar} busy={busy} />}
  </div>
}
