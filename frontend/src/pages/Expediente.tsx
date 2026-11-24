import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Aviso, Carga, EstadoBadge, Icon, Plazo, RecursoError, SinDatos, Titulo } from '../components/Ui'
import { api, mensaje, useRecurso } from '../services/api'
import type { Documento, Expediente, Generacion, Previsualizacion } from '../services/api'
import { fecha, moneda } from '../services/formato'
import { ActuacionesPanel } from './Actuaciones'
import { AlertasContrato } from './Alertas'

const sections = [
  ['resumen', 'Resumen'],
  ['seguimiento', 'Seguimiento'],
  ['alertas', 'Alertas y prioridad'],
  ['actuaciones', 'Actuaciones'],
  ['documentos', 'Documentos'],
  ['requerimientos', 'Requerimientos'],
  ['historial', 'Historial'],
] as const

const reqStages = {
  'primer-requerimiento': 'Primer requerimiento',
  'segundo-requerimiento': 'Segundo requerimiento',
  'tercer-requerimiento': 'Tercer requerimiento',
} as const

type Requerimiento = keyof typeof reqStages

function Campo({ label, children }: { label: string; children?: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children || '—'}</dd></div>
}

function Resumen({ contrato }: { contrato: Expediente }) {
  return <div className="detail-grid">
    <section className="panel detail-panel"><h2>Datos del contrato</h2>
      <dl className="definition-grid">
        <Campo label="Entidad">{contrato.entidad.nombre}</Campo>
        <Campo label="Tipo de contrato">{contrato.tipo_contrato}</Campo>
        <Campo label="Modalidad">{contrato.modalidad_contratacion}</Campo>
        <Campo label="Contratista">{contrato.contratista_nombre}</Campo>
        <Campo label="Identificación">{contrato.contratista_identificacion}</Campo>
        <Campo label="Supervisor">{contrato.supervisor_nombre}</Campo>
        <Campo label="Valor inicial">{moneda(contrato.valor_inicial)}</Campo>
        <Campo label="Valor actual">{moneda(contrato.valor_actual)}</Campo>
      </dl>
      <div className="detail-text"><h3>Objeto contractual</h3><p>{contrato.objeto_contractual}</p></div>
      {contrato.observaciones && <div className="detail-text"><h3>Observaciones</h3><p>{contrato.observaciones}</p></div>}
    </section>
    <section className="panel detail-panel"><h2>Fechas registradas</h2>
      <dl className="definition-stack">
        <Campo label="Suscripción">{fecha(contrato.fecha_suscripcion)}</Campo>
        <Campo label="Inicio contractual">{fecha(contrato.fecha_inicio)}</Campo>
        <Campo label="Terminación contractual">{fecha(contrato.fecha_terminacion)}</Campo>
        <Campo label="Asignación al abogado">{fecha(contrato.fecha_asignacion)}</Campo>
      </dl>
    </section>
  </div>
}

function Seguimiento({ contrato, recargar }: { contrato: Expediente; recargar: () => void }) {
  const [confirmado, setConfirmado] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function iniciar() {
    setBusy(true)
    setError('')
    try {
      await api<Expediente>(`contratos/${contrato.id}/seguimiento/`, { method: 'POST', body: { confirmado } })
      recargar()
    } catch (err) { setError(mensaje(err)) } finally { setBusy(false) }
  }

  return <section className="panel detail-panel">
    <h2>Seguimiento y vencimientos</h2>
    {contrato.seguimiento.length ? <ol className="timeline">
      {contrato.seguimiento.map((etapa) => <li key={etapa.id}>
        <span className="timeline-marker"><Icon name={etapa.fecha_finalizacion ? 'check' : 'clock'} size={16} /></span>
        <div className="timeline-entry">
          <div className="timeline-title"><h3>{etapa.estado.nombre}</h3>
            <span className={`badge ${etapa.fecha_finalizacion ? 'badge-slate' : 'badge-blue'}`}>
              {etapa.fecha_finalizacion ? 'Finalizada' : 'En seguimiento'}</span></div>
          <dl className="definition-grid timeline-dates">
            <Campo label="Inicio">{fecha(etapa.fecha_inicio)}</Campo>
            <Campo label="Plazo configurado">{etapa.plazo_dias_habiles ? `${etapa.plazo_dias_habiles} días hábiles` : 'Sin plazo'}</Campo>
            <Campo label="Vencimiento operativo"><Plazo plazo={etapa.vencimiento_operativo} /></Campo>
            <Campo label="Finalización">{fecha(etapa.fecha_finalizacion)}</Campo>
          </dl>
          {etapa.vencimiento_operativo?.advertencia && <Aviso>{etapa.vencimiento_operativo.advertencia}</Aviso>}
          {etapa.vencimiento_operativo?.fecha_original !== etapa.vencimiento_operativo?.fecha_teorica &&
            <p className="subtle mt-2">Vencimiento original {fecha(etapa.vencimiento_operativo?.fecha_original)} · Teórico {fecha(etapa.vencimiento_operativo?.fecha_teorica)}</p>}
        </div>
      </li>)}
    </ol> : <SinDatos icon="clock" title="Seguimiento sin iniciar"
      text={contrato.fecha_asignacion ? 'El contrato está registrado. Puedes iniciar el seguimiento si la asignación ya ocurrió.'
        : 'Registra la fecha real de asignación en la edición del contrato para iniciar el seguimiento.'} />}
    {contrato.permisos.iniciar_seguimiento && <div className="start-tracking">
      {error && <Aviso tipo="error">{error}</Aviso>}
      <label className="checkbox-label"><input type="checkbox" checked={confirmado} disabled={busy}
        onChange={(e) => setConfirmado(e.target.checked)} />
        Confirmo que la fecha de asignación es real y que no hay actuaciones previas.</label>
      <button type="button" className="button button-primary" onClick={iniciar} disabled={busy || !confirmado}>
        <Icon name="clock" size={17} />{busy ? 'Iniciando…' : 'Iniciar seguimiento'}</button>
    </div>}
  </section>
}

function Descarga({ documento, docx }: { documento: Documento; docx?: boolean }) {
  if (!documento.descarga_url) return <span className="subtle">Sin descarga disponible</span>
  return <a className="button button-secondary button-small" href={documento.descarga_url} download
    aria-label={`${docx ? 'Descargar DOCX' : 'Descargar documento'} ${documento.nombre_original}`}>
    <Icon name="download" size={16} />{docx ? 'Descargar DOCX' : 'Descargar'}
  </a>
}

function Documentos({ contrato }: { contrato: Expediente }) {
  return <section className="panel detail-panel">
    <h2>Documentos asociados</h2>
    {!contrato.permisos.ver_documentos ? <Aviso>Tu usuario no tiene acceso a los documentos de este expediente.</Aviso> :
      contrato.documentos.length ? <ul className="file-list">
        {contrato.documentos.map((documento) => <li key={documento.id}>
          <span className="heading-icon tint-blue"><Icon name="file" size={18} /></span>
          <div className="file-summary"><strong>{documento.nombre_original}</strong>
            <small>{documento.tipo_documento} · Versión {documento.version} · {fecha(documento.fecha_carga)}</small></div>
          <span className="badge badge-slate">{documento.estado_revision}</span>
          <Descarga documento={documento} />
        </li>)}
      </ul> : <SinDatos icon="file" title="Aún no hay documentos asociados" text="Los soportes y borradores registrados aparecerán aquí." />}
  </section>
}

function Historial({ contrato }: { contrato: Expediente }) {
  return <section className="panel detail-panel">
    <h2>Historial de estados</h2>
    {!contrato.permisos.ver_historial ? <Aviso>Tu cuenta no tiene acceso al historial de este expediente.</Aviso> :
      contrato.historial.length ? <ol className="timeline">
      {contrato.historial.map((evento) => <li key={evento.id}>
        <span className="timeline-marker"><Icon name="activity" size={16} /></span>
        <div className="timeline-entry">
          <small className="subtle">{fecha(evento.fecha_aprobacion || evento.fecha_solicitud)}</small>
          <div className="history-transition">
            <EstadoBadge estado={evento.estado_anterior} /><Icon name="arrow" size={16} /><EstadoBadge estado={evento.estado_nuevo} />
            <span className="badge badge-slate">{evento.resultado.replaceAll('_', ' ')}</span>
          </div>
          <p>{evento.motivo}</p>{evento.observaciones && <p className="subtle">{evento.observaciones}</p>}
        </div>
      </li>)}
    </ol> : <SinDatos icon="activity" title="Aún no hay cambios de estado" text="Las actuaciones registradas aparecerán en orden cronológico." />}
  </section>
}

function nombreCampo(value: string) {
  return value.replaceAll('_', ' ').replace(/^./, (first) => first.toLocaleUpperCase('es-CO'))
}

function mostrarValor(value: unknown): string {
  if (Array.isArray(value)) return value.map(mostrarValor).join(', ')
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  if (typeof value === 'number' || typeof value === 'string') return String(value)
  return '—'
}

function RequerimientoPanel({ contrato, tipo }: { contrato: Expediente; tipo: Requerimiento }) {
  const path = `contratos/${contrato.id}/${tipo}/`
  const preview = useRecurso<Previsualizacion>(path)
  const operation = useRef('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [generated, setGenerated] = useState<Generacion>()
  const data = preview.data

  async function generar() {
    operation.current ||= crypto.randomUUID()
    setBusy(true)
    setError('')
    try {
      const result = await api<Generacion>(path, { method: 'POST', body: { operacion: operation.current } })
      operation.current = ''
      setGenerated(result)
      preview.recargar()
    } catch (err) {
      setError(mensaje(err))
    } finally { setBusy(false) }
  }

  return <section className="panel detail-panel">
    <div className="section-head"><div><h2>{reqStages[tipo]}</h2>
      <p className="subtle">Revisa los datos antes de generar el borrador institucional.</p></div>
      {data?.disponible && contrato.permisos.generar && <button type="button" className="button button-primary" onClick={generar} disabled={busy}>
        <Icon name="file" size={18} />{busy ? 'Generando DOCX…' : 'Generar borrador DOCX'}</button>}
    </div>
    {error && <Aviso tipo="error">{error}</Aviso>}
    {generated && <Aviso tipo="exito"><div className="success-download"><span>Borrador generado. Versión {generated.version}.</span>
      <Descarga documento={generated.documento} docx /></div></Aviso>}
    {preview.loading && <Carga texto="Consultando datos y versiones…" />}
    {preview.error && <RecursoError error={preview.error} recargar={preview.recargar} />}
    {data && <>
      {!data.disponible && <Aviso>{data.motivo || 'La generación aún no está disponible para este expediente.'}</Aviso>}
      {data.campos.length > 0 && <details className="preview-details" open>
        <summary>Vista previa de campos <span className="badge badge-amber">{data.campos.filter((entry) => entry.pendiente).length} pendientes</span></summary>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Campos de la plantilla">
          <table className="data-table preview-table"><thead><tr><th>Campo</th><th>Dato disponible</th><th>Fuente</th></tr></thead>
            <tbody>{data.campos.map((entry) => <tr key={entry.nombre}>
              <td>{nombreCampo(entry.nombre)}</td><td>{entry.pendiente ? <span className="badge badge-amber">Pendiente</span> : mostrarValor(entry.valor)}</td>
              <td className="subtle">{entry.fuente || '—'}</td>
            </tr>)}</tbody></table>
        </div>
      </details>}
      <h3 className="versions-heading">Versiones generadas</h3>
      {data.versiones.length ? <ul className="file-list">
        {data.versiones.map((item) => <li key={item.id}>
          <span className="heading-icon tint-blue"><Icon name="file" size={18} /></span>
          <div className="file-summary"><strong>Versión {item.version} · {item.documento.nombre_original}</strong>
            <small>{fecha(item.documento.fecha_carga)} · {item.campos_pendientes.length} campos pendientes</small></div>
          <Descarga documento={item.documento} docx />
        </li>)}
      </ul> : <p className="subtle">Todavía no hay borradores para este requerimiento.</p>}
    </>}
  </section>
}

function Requerimientos({ contrato }: { contrato: Expediente }) {
  const codigo = contrato.estado_actual.codigo
  const inicial: Requerimiento = codigo in reqStages ? codigo as Requerimiento : 'primer-requerimiento'
  const [tipo, setTipo] = useState<Requerimiento>(inicial)
  if (!contrato.permisos.ver_documentos) return <Aviso>Tu cuenta no tiene acceso a los requerimientos de este expediente.</Aviso>
  return <>
    <label className="field requirement-type"><span>Tipo de requerimiento</span>
      <select value={tipo} onChange={(event) => setTipo(event.target.value as Requerimiento)}>
        {Object.entries(reqStages).map(([key, label]) => <option value={key} key={key}>{label}</option>)}
      </select>
    </label>
    <RequerimientoPanel key={tipo} contrato={contrato} tipo={tipo} />
  </>
}

export function ExpedientePage({ id, query = '' }: { id: number; query?: string }) {
  const expediente = useRecurso<Expediente>(`contratos/${id}/`)
  const contrato = expediente.data
  const params = new URLSearchParams(query)
  const active = sections.find(([key]) => key === params.get('seccion'))?.[0] ?? 'resumen'
  const guardado = params.get('guardado')

  return <>
    <a className="back-link" href="#/contratos"><Icon name="back" size={17} /> Volver a contratos</a>
    {expediente.loading && <Carga texto="Abriendo expediente…" />}
    {expediente.error && <RecursoError error={expediente.error} recargar={expediente.recargar} />}
    {contrato && <>
      <Titulo etiqueta="Expediente contractual" title={`${contrato.numero_contrato} · ${contrato.anio_contractual}`}
        description={contrato.contratista_nombre} actions={<>
          {contrato.permisos.ver_documentos && <a className="button button-primary" href={`#/contratos/${id}/revisiones`}>
            <Icon name="search" size={18} /> Revisión inteligente
          </a>}
          {contrato.permisos.editar && <a className="button button-secondary" href={`#/contratos/${id}/editar`}>
            <Icon name="edit" size={18} /> Editar contrato
          </a>}
        </>} />
      {guardado && <Aviso tipo="exito">{guardado === 'creado' ? 'Contrato registrado.' : 'Cambios guardados.'}</Aviso>}
      <div className="panel detail-summary">
        <div><span>Estado actual</span><EstadoBadge estado={contrato.estado_actual} /></div>
        <div><span>Terminación contractual</span><strong>{fecha(contrato.fecha_terminacion)}</strong></div>
        <div><span>Vencimiento operativo</span><Plazo plazo={contrato.vencimiento_operativo} /></div>
      </div>
      {contrato.advertencias.length > 0 && <Aviso>
        {contrato.advertencias.length > 1 ? <ul>{contrato.advertencias.map((aviso) => <li key={aviso}>{aviso}</li>)}</ul> : contrato.advertencias[0]}
      </Aviso>}
      <nav className="tabs" aria-label="Secciones del expediente">
        {sections.filter(([key]) => !['actuaciones', 'alertas'].includes(key) || contrato.permisos.ver_historial)
          .map(([key, label]) => <a key={key} className={active === key ? 'tab tab-active' : 'tab'}
          aria-current={active === key ? 'page' : undefined} href={`#/contratos/${id}?seccion=${key}`}>{label}</a>)}
      </nav>
      {active === 'resumen' && <Resumen contrato={contrato} />}
      {active === 'seguimiento' && <Seguimiento contrato={contrato} recargar={expediente.recargar} />}
      {active === 'alertas' && (contrato.permisos.ver_historial
        ? <AlertasContrato key={contrato.id} id={contrato.id} />
        : <Aviso>Tu cuenta no tiene permiso para consultar las alertas del expediente.</Aviso>)}
      {active === 'actuaciones' && (contrato.permisos.ver_historial
        ? <ActuacionesPanel contrato={contrato} />
        : <Aviso>Tu cuenta no tiene permiso para consultar las actuaciones.</Aviso>)}
      {active === 'documentos' && <Documentos contrato={contrato} />}
      {active === 'requerimientos' && <Requerimientos contrato={contrato} />}
      {active === 'historial' && <Historial contrato={contrato} />}
    </>}
  </>
}
