import { useState } from 'react'
import type { FormEvent } from 'react'
import { Aviso, Carga, Icon, RecursoError, Titulo } from '../components/Ui'
import { api, ErrorApi, mensaje, useRecurso } from '../services/api'
import type { CamposContrato, Catalogos, Expediente } from '../services/api'

const groups: Array<{ title: string; fields: Array<{
  name: keyof CamposContrato; label: string; required?: boolean; max?: number; type?: string
}> }> = [
  { title: 'Información contractual', fields: [
    { name: 'numero_contrato', label: 'Número de contrato', required: true, max: 80 },
    { name: 'anio_contractual', label: 'Vigencia', required: true, type: 'year' },
    { name: 'tipo_contrato', label: 'Tipo de contrato', required: true, max: 120 },
    { name: 'modalidad_contratacion', label: 'Modalidad de contratación', required: true, max: 150 },
    { name: 'objeto_contractual', label: 'Objeto contractual', required: true, type: 'textarea' },
  ] },
  { title: 'Partes y valores', fields: [
    { name: 'contratista_nombre', label: 'Nombre del contratista', required: true, max: 255 },
    { name: 'contratista_identificacion', label: 'Identificación del contratista', required: true, max: 80 },
    { name: 'supervisor_nombre', label: 'Supervisor', max: 255 },
    { name: 'valor_inicial', label: 'Valor inicial (COP)', required: true, type: 'money' },
    { name: 'valor_actual', label: 'Valor actual (COP)', required: true, type: 'money' },
  ] },
  { title: 'Fechas y observaciones', fields: [
    { name: 'fecha_suscripcion', label: 'Fecha de suscripción', required: true, type: 'date' },
    { name: 'fecha_inicio', label: 'Fecha de inicio', type: 'date' },
    { name: 'fecha_terminacion', label: 'Fecha de terminación', type: 'date' },
    { name: 'fecha_asignacion', label: 'Fecha de asignación', type: 'date' },
    { name: 'observaciones', label: 'Observaciones', type: 'textarea' },
  ] },
]

const optionalDates = new Set(['fecha_inicio', 'fecha_terminacion', 'fecha_asignacion'])

function Formulario({ contrato, entidad, recargar }: { contrato?: Expediente; entidad: string; recargar: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error>()
  const fieldErrors = error instanceof ErrorApi ? error.campos : {}
  const conflict = error instanceof ErrorApi && error.status === 409
  const back = contrato ? `#/contratos/${contrato.id}` : '#/contratos'

  async function guardar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const campos = Object.fromEntries(Array.from(values.entries(), ([key, value]) => {
      const valueString = String(value).trim()
      if (key === 'anio_contractual') return [key, Number(valueString)]
      if (optionalDates.has(key)) return [key, valueString || null]
      if (key === 'valor_actual' || key === 'valor_inicial') return [key, valueString.replace(',', '.')]
      return [key, valueString]
    }))
    const body = contrato ? { ...campos, version_registro: contrato.version_registro } : campos
    setBusy(true)
    setError(undefined)
    try {
      const result = await api<Expediente>(contrato ? `contratos/${contrato.id}/` : 'contratos/', {
        method: contrato ? 'PATCH' : 'POST', body,
      })
      window.location.hash = `/contratos/${result.id}?guardado=${contrato ? 'editado' : 'creado'}`
    } catch (err) { setError(err instanceof Error ? err : new Error(mensaje(err))) } finally { setBusy(false) }
  }

  return <form className="contract-form" onSubmit={guardar}>
    <div className="form-entity panel"><Icon name="briefcase" size={18} /><span className="subtle">Entidad</span><strong>{entidad}</strong>
      {contrato && <small className="subtle">Versión {contrato.version_registro}</small>}</div>
    {error && <Aviso tipo="error">
      <p>{error.message}</p>
      {conflict && <button className="text-action mt-1" type="button" onClick={recargar}>Cargar última versión y descartar este formulario</button>}
    </Aviso>}
    {groups.map((group) => <fieldset className="panel form-panel" key={group.title} disabled={busy}>
      <legend className="sr-only">{group.title}</legend><h2>{group.title}</h2>
      <div className="form-grid">
        {group.fields.map(({ name, label, required, max, type }) => {
          const saved = contrato?.[name]
          const current = saved ?? (name === 'anio_contractual' ? new Date().getFullYear() : '')
          const locked = name === 'fecha_asignacion' && contrato && !contrato.puede_editar_fecha_asignacion
          const errorId = `${name}-error`
          const common = { name, defaultValue: current, required, disabled: Boolean(locked),
            'aria-invalid': fieldErrors[name] ? true : undefined,
            'aria-describedby': fieldErrors[name] ? errorId : undefined }
          return <label className={`field ${type === 'textarea' ? 'form-wide' : ''}`} key={name}>
            <span>{label}{required ? <span className="required" aria-hidden="true"> *</span> : null}</span>
            {type === 'textarea' ? <textarea {...common} rows={3} /> :
              <input {...common} maxLength={max ?? (type === 'money' ? 21 : undefined)}
                type={type === 'date' ? 'date' : type === 'year' ? 'number' : 'text'}
                min={type === 'year' ? 1900 : undefined} max={type === 'year' ? 9999 : undefined}
                inputMode={type === 'money' ? 'decimal' : undefined}
                pattern={type === 'money' ? '[0-9]+([,.][0-9]{1,2})?' : undefined}
                title={type === 'money' ? 'Indica el valor sin separadores de miles y con hasta dos decimales.' : undefined} />}
            {locked && <small className="subtle">La asignación queda fija al iniciar el seguimiento.</small>}
            {fieldErrors[name] && <small className="field-error" id={errorId}>{fieldErrors[name]}</small>}
          </label>
        })}
      </div>
    </fieldset>)}
    <div className="form-actions">
      <a href={back} className="button button-secondary">Cancelar</a>
      <button type="submit" className="button button-primary" disabled={busy || conflict}>
        <Icon name="check" size={18} />{busy ? 'Guardando…' : contrato ? 'Guardar cambios' : 'Registrar contrato'}
      </button>
    </div>
  </form>
}

export function ContratoFormPage({ id }: { id?: number }) {
  const contract = useRecurso<Expediente>(id ? `contratos/${id}/` : null)
  const catalogs = useRecurso<Catalogos>(id ? null : 'catalogos/')
  const state = id ? contract : catalogs
  const entidad = contract.data?.entidad ?? catalogs.data?.entidad
  const permitido = id ? contract.data?.permisos.editar : catalogs.data?.permisos?.crear
  const back = id ? `#/contratos/${id}` : '#/contratos'
  return <>
    <a href={back} className="back-link"><Icon name="back" size={17} />{id ? 'Volver al expediente' : 'Volver a contratos'}</a>
    <Titulo etiqueta="Cartera contractual" title={id ? 'Editar contrato' : 'Nuevo contrato'}
      description={id ? 'Actualiza la información general del expediente.' : 'Registra un expediente ficticio para la demostración.'} />
    {state.loading && <Carga />}
    {state.error && <RecursoError error={state.error} recargar={state.recargar} />}
    {state.data && (!permitido ? <Aviso tipo="error">Tu cuenta no puede guardar este contrato.</Aviso>
      : !entidad ? <Aviso>Hace falta una entidad activa para registrar contratos.</Aviso>
      : <Formulario key={contract.data?.version_registro ?? 'nuevo'} contrato={contract.data} entidad={entidad.nombre} recargar={state.recargar} />)}
  </>
}
