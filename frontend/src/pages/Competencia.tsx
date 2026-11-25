import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Aviso, Carga, RecursoError } from '../components/Ui'
import { api, ErrorApi, mensaje, useRecurso } from '../services/api'
import { fecha } from '../services/formato'

const fechas = {
  fecha_base: 'Fecha base',
  fin_bilateral: 'Fin de etapa bilateral',
  fin_unilateral: 'Fin de etapa unilateral',
  inicio_judicial: 'Inicio de término judicial',
  fin_judicial: 'Fin de término judicial',
  limite_competencia: 'Fecha límite de competencia',
} as const
type NombreFecha = keyof typeof fechas
const nombresFechas = Object.keys(fechas) as NombreFecha[]
type Accion = 'calcular' | 'verificar' | 'rectificar'
type Version = {
  id: number
  version: number
  estado: string
  estado_label: string
  calculado_en: string
  verificado_en: string | null
  verificado_por: string | null
}
type Actual = Version & {
  fechas: Record<NombreFecha, string | null>
  reglas: string[]
  advertencias: string[]
  faltantes: string[]
  fuentes: Record<string, string>
  version_reglas: string
  fundamento: string
  motivo: string
  vigente: boolean
  puede_verificar: boolean
  puede_rectificar: boolean
}
type Insumos = Record<string, string | number | null>
type Ficha = {
  actual: Actual | null
  versiones: Version[]
  insumos: Insumos
  puede_editar: boolean
  fecha_confirmada: string | null
  motivo_revision: string | null
  anios_aprobados: number[]
  anios_pendientes: number[]
  fuentes: Record<string, string>
  soportes: { id: number; nombre_original: string }[]
}
type Guardar = (accion: Accion, datos: Record<string, unknown>) => Promise<void>
type Opcion = readonly [string, string]
const siNo: Opcion[] = [['si', 'Sí'], ['no', 'No']]
const regimenes: Opcion[] = [['art11_cpaca', 'Ley 1150, art. 11 y CPACA'], ['otro', 'Otro régimen']]
const origenes: Opcion[] = [
  ['ejecucion', 'Expiración de la ejecución'], ['efectiva', 'Terminación efectiva'],
  ['acto', 'Acto administrativo'], ['acuerdo', 'Acuerdo'], ['otra', 'Otra fecha jurídicamente relevante'],
]
const unidades: Opcion[] = [
  ['dias_calendario', 'Días calendario'], ['meses', 'Meses'], ['anios', 'Años'],
  ['dias_habiles', 'Días hábiles · requiere revisión'],
]
const liquidaciones: Opcion[] = [
  ['ninguna', 'Ninguna'], ['bilateral', 'Bilateral'], ['unilateral', 'Unilateral'],
]

function campo(insumos: Insumos, nombre: string) {
  return String(insumos[nombre] ?? '')
}

function OpcionCampo({ nombre, titulo, opciones, valor, alCambiar }: {
  nombre: string
  titulo: string
  opciones: Opcion[]
  valor: string
  alCambiar?: (valor: string) => void
}) {
  return <label className="field">
    <span>{titulo}</span>
    <select name={nombre} defaultValue={valor} onChange={alCambiar ? (event) => alCambiar(event.currentTarget.value) : undefined}>
      <option value="">Por determinar</option>
      {opciones.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
    </select>
  </label>
}

function FechaCampo({ nombre, titulo, valor }: { nombre: string; titulo: string; valor: string }) {
  return <label className="field">
    <span>{titulo}</span><input type="date" name={nombre} defaultValue={valor} />
  </label>
}

function Propuesta({ ficha, busy, guardar }: { ficha: Ficha; busy: boolean; guardar: Guardar }) {
  const { insumos } = ficha
  const valor = (nombre: string) => campo(insumos, nombre)
  const [origen, setOrigen] = useState(valor('origen') || (valor('fecha_terminacion') ? 'ejecucion' : ''))
  const [pacto, setPacto] = useState(valor('pacto'))
  const [liquidacion, setLiquidacion] = useState(valor('liquidacion'))
  async function calcular(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formulario = new FormData(event.currentTarget)
    const datos: Record<string, unknown> = { fecha_terminacion: insumos.fecha_terminacion || null }
    for (const [nombre, contenido] of formulario.entries()) {
      const texto = String(contenido).trim()
      if (nombre === 'cantidad' || nombre === 'soporte_terminacion') {
        datos[nombre] = texto ? Number(texto) : null
      } else if (nombre.startsWith('fecha_') || ['firma_acta', 'ejecutoria_acto', 'referencia_pacto'].includes(nombre)) {
        datos[nombre] = texto || null
      } else {
        datos[nombre] = texto
      }
    }
    await guardar('calcular', { datos })
  }
  return <details className="ri-card" open={!ficha.actual ? true : undefined}>
    <summary>{ficha.actual ? 'Actualizar insumos y calcular nueva propuesta' : 'Calcular una propuesta jurídica'}</summary>
    <div className="ri-card-body">
      <form onSubmit={calcular}>
        <fieldset className="ri-fields-form" disabled={busy}>
          <OpcionCampo nombre="sujeto" titulo="¿El contrato requiere liquidación?" opciones={siNo} valor={valor('sujeto')} />
          <OpcionCampo nombre="regimen" titulo="Régimen aplicable" opciones={regimenes} valor={valor('regimen')} />
          <OpcionCampo nombre="origen" titulo="Origen de la fecha base" opciones={origenes} valor={origen} alCambiar={setOrigen} />
          {origen === 'ejecucion' && <div>
            <strong>Terminación registrada:</strong> {fecha(valor('fecha_terminacion') || null)}
            {!valor('fecha_terminacion') && <p className="subtle">Registra la fecha en los datos del contrato o selecciona otra base documentada.</p>}
          </div>}
          {origen === 'efectiva' && <FechaCampo nombre="fecha_efectiva" titulo="Terminación efectiva" valor={valor('fecha_efectiva')} />}
          {['acto', 'acuerdo', 'otra'].includes(origen) && <FechaCampo nombre="fecha_referencia" titulo="Fecha del acto, acuerdo o referencia" valor={valor('fecha_referencia')} />}
          {origen && origen !== 'ejecucion' && ficha.soportes.length > 0 && <label className="field">
            <span>Soporte de la fecha base</span><select name="soporte_terminacion" defaultValue={valor('soporte_terminacion')}>
              <option value="">Indicar referencia escrita debajo</option>
              {ficha.soportes.map((soporte) => <option key={soporte.id} value={soporte.id}>{soporte.nombre_original}</option>)}
            </select>
          </label>}
          <OpcionCampo nombre="pacto" titulo="¿Existe plazo bilateral pactado?" opciones={siNo} valor={pacto} alCambiar={setPacto} />
          {pacto === 'si' && <>
            <label className="field"><span>Cantidad pactada</span>
              <input type="number" min="1" max="10000" name="cantidad" defaultValue={valor('cantidad')} />
            </label>
            <OpcionCampo nombre="unidad" titulo="Unidad pactada" opciones={unidades} valor={valor('unidad')} />
            <FechaCampo nombre="referencia_pacto" titulo="Fecha de referencia del pacto" valor={valor('referencia_pacto')} />
          </>}
          <OpcionCampo nombre="liquidacion" titulo="Liquidación previa" opciones={liquidaciones} valor={liquidacion} alCambiar={setLiquidacion} />
          {['bilateral', 'unilateral'].includes(liquidacion) && <>
            <FechaCampo nombre="fecha_liquidacion" titulo="Fecha del acta o acto de liquidación" valor={valor('fecha_liquidacion')} />
            {liquidacion === 'bilateral'
              ? <FechaCampo nombre="firma_acta" titulo="Fecha de firma bilateral" valor={valor('firma_acta')} />
              : <FechaCampo nombre="ejecutoria_acto" titulo="Fecha de ejecutoria del acto" valor={valor('ejecutoria_acto')} />}
          </>}
          <OpcionCampo nombre="incidencias" titulo="¿Hay demanda, conciliación, suspensión u otra incidencia?" opciones={siNo} valor={valor('incidencias')} />
          <label className="field"><span>Referencias documentales y cláusulas</span>
            <textarea name="referencias" rows={2} maxLength={4000} defaultValue={valor('referencias')} />
          </label>
          <label className="field"><span>Observaciones jurídicas</span>
            <textarea name="observaciones" rows={2} maxLength={4000} defaultValue={valor('observaciones')} />
          </label>
          <button type="submit" className="button button-primary button-small">{busy ? 'Guardando…' : 'Calcular propuesta'}</button>
        </fieldset>
      </form>
    </div>
  </details>
}

function Revision({ actual, busy, guardar, accion }: {
  actual: Actual; busy: boolean; guardar: Guardar; accion: 'rectificar' | 'verificar'
}) {
  const rectificar = accion === 'rectificar'
  const titulo = rectificar ? 'Preparar rectificación jurídica' : 'Verificar propuesta como abogado'
  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const datos = new FormData(event.currentTarget)
    const campos: Record<string, unknown> = {
      fundamento: String(datos.get('fundamento') ?? '').trim(), confirmar: datos.has('confirmar'),
    }
    if (rectificar) {
      campos.motivo = String(datos.get('motivo') ?? '').trim()
      campos.rectificacion = Object.fromEntries(nombresFechas.map((nombre) => [nombre, datos.get(nombre) || null]))
    }
    await guardar(accion, campos)
  }
  return <details className="ri-card">
    <summary>{titulo}</summary>
    <div className="ri-card-body">
      {rectificar && <p className="subtle">La rectificación crea otra propuesta. Revísala y verifícala antes de generar alertas.</p>}
      <form onSubmit={enviar}>
        <fieldset className="ri-fields-form" disabled={busy}>
          {rectificar && <>
            {nombresFechas.map((nombre) => <FechaCampo key={nombre} nombre={nombre} titulo={fechas[nombre]} valor={actual.fechas[nombre] ?? ''} />)}
            <label className="field"><span>Motivo de la rectificación</span><textarea name="motivo" rows={2} maxLength={1000} required /></label>
          </>}
          <label className="field"><span>Fundamento jurídico y soporte</span>
            <textarea name="fundamento" rows={3} maxLength={4000} required />
          </label>
          <label className="ri-inline"><input name="confirmar" type="checkbox" required />
            {rectificar ? 'Revisé los soportes del ajuste.' : 'Revisé el expediente y confirmo la propuesta jurídica.'}
          </label>
          <button type="submit" className="button button-primary button-small">
            {busy ? 'Guardando…' : rectificar ? 'Guardar propuesta rectificada' : 'Confirmar verificación'}
          </button>
        </fieldset>
      </form>
    </div>
  </details>
}

function Resultado({ actual, ficha }: { actual: Actual; ficha: Ficha }) {
  const verificado = actual.estado === 'verificado_por_abogado'
  return <div className="ri-stack">
    <div className="ri-inline">
      <span className={`badge badge-${actual.vigente ? 'blue' : 'amber'}`}>{actual.vigente ? 'Verificado y vigente' : verificado ? 'Verificado histórico' : actual.estado_label}</span>
      <span className="subtle">Versión {actual.version}{actual.version_reglas && ` · Regla ${actual.version_reglas}`}</span>
    </div>
    <dl className="definition-grid">
      {nombresFechas.map((nombre) => <div key={nombre}>
        <dt>{fechas[nombre]}{nombre === 'limite_competencia' && !ficha.fecha_confirmada ? verificado ? ' · histórica' : ' · preliminar' : ''}</dt>
        <dd>{actual.fechas[nombre] ? fecha(actual.fechas[nombre]) : 'Sin determinar'}</dd>
      </div>)}
    </dl>
    {actual.faltantes.length > 0 && <Aviso><strong>Información requerida</strong>
      <ul>{actual.faltantes.map((dato) => <li key={dato}>{dato}</li>)}</ul>
    </Aviso>}
    {(actual.advertencias.length > 0 || ficha.motivo_revision) && <Aviso>
      <strong>Revisión de fechas</strong>
      <ul>{actual.advertencias.map((aviso) => <li key={aviso}>{aviso}</li>)}
        {ficha.motivo_revision && <li>{ficha.motivo_revision}</li>}
      </ul>
    </Aviso>}
    {ficha.anios_pendientes.length > 0 && <p>
      <strong>Calendarios por aprobar:</strong> {ficha.anios_pendientes.join(', ')}. Calcula de nuevo después de su aprobación.
    </p>}
    {actual.verificado_por && <p><strong>Verificó:</strong> {actual.verificado_por} · {fecha(actual.verificado_en)}</p>}
    {actual.fundamento && <p><strong>Fundamento de la verificación:</strong> {actual.fundamento}</p>}
    {actual.motivo && <p><strong>Motivo:</strong> {actual.motivo}</p>}
    {actual.reglas.length > 0 && <details className="ri-card">
      <summary>Cómo se obtuvo la propuesta</summary><div className="ri-card-body">
        <ul>{actual.reglas.map((regla, indice) => <li key={indice}>{regla}</li>)}</ul>
      </div>
    </details>}
  </div>
}

export function CompetenciaPanel({ id, alGuardar }: { id: number; alGuardar: () => void }) {
  const path = `contratos/${id}/competencia/`
  const recurso = useRecurso<Ficha>(path)
  const [guardado, setGuardado] = useState<Ficha>()
  const [busy, setBusy] = useState(false)
  const [aviso, setAviso] = useState<{ tipo: 'exito' | 'error'; mensaje: string }>()
  const operacionPendiente = useRef<{ huella: string; operacion: string } | null>(null)
  const ficha = guardado ?? recurso.data
  async function guardar(accion: Accion, campos: Record<string, unknown>) {
    if (!ficha || busy) return
    const cuerpo = { accion, anterior_id: ficha.actual?.id ?? null, ...campos }
    const huella = JSON.stringify(cuerpo)
    const operacion = operacionPendiente.current?.huella === huella
      ? operacionPendiente.current.operacion : crypto.randomUUID()
    operacionPendiente.current = { huella, operacion }
    setBusy(true)
    setAviso(undefined)
    try {
      const actualizado = await api<Ficha>(path, { method: 'POST', body: { ...cuerpo, operacion } })
      setGuardado(actualizado)
      setAviso({ tipo: 'exito', mensaje: accion === 'verificar' ? 'Verificación jurídica registrada.' : 'Propuesta guardada para revisión.' })
      operacionPendiente.current = null
      alGuardar()
    } catch (error) {
      const camposError = error instanceof ErrorApi ? Object.values(error.campos).join(' ') : ''
      setAviso({ tipo: 'error', mensaje: camposError || mensaje(error) })
    } finally {
      setBusy(false)
    }
  }
  function recargar() {
    setGuardado(undefined)
    setAviso(undefined)
    operacionPendiente.current = null
    recurso.recargar()
  }
  return <section className="panel detail-panel ri-stack" aria-labelledby="titulo-competencia">
    <div className="section-head"><div><h2 id="titulo-competencia">Pérdida de competencia</h2>
      <p className="subtle">Proyecta los hitos jurídicos y verifica la fecha límite con base en el expediente.</p></div>
    </div>
    {recurso.loading && !ficha && <Carga texto="Consultando fechas jurídicas…" />}
    {recurso.error && !ficha && <RecursoError error={recurso.error} recargar={recargar} />}
    {aviso && <Aviso tipo={aviso.tipo}>{aviso.mensaje}{aviso.tipo === 'error' && (
      <button type="button" className="text-action ml-3" onClick={recargar}>Actualizar ficha</button>
    )}</Aviso>}
    {ficha && <>
      {ficha.actual ? <Resultado actual={ficha.actual} ficha={ficha} /> : <p className="subtle">El expediente todavía no tiene un cálculo jurídico.</p>}
      {!ficha.fecha_confirmada && <p className="subtle">Las alertas de competencia se activan al verificar una fecha límite vigente.</p>}
      {ficha.puede_editar && <Propuesta key={ficha.actual?.id ?? 'nueva'} ficha={ficha} busy={busy} guardar={guardar} />}
      {ficha.actual?.puede_rectificar && <Revision key={`rectificar-${ficha.actual.id}`} actual={ficha.actual} busy={busy} guardar={guardar} accion="rectificar" />}
      {ficha.actual?.puede_verificar && <Revision key={`verificar-${ficha.actual.id}`} actual={ficha.actual} busy={busy} guardar={guardar} accion="verificar" />}
      <details className="ri-card">
        <summary>Fuentes, calendario y versiones</summary>
        <div className="ri-card-body ri-stack">
          <p>Calendarios aprobados: {ficha.anios_aprobados.join(', ') || 'sin años aprobados'}.</p>
          <ul>{Object.entries(ficha.actual?.fuentes ?? ficha.fuentes).map(([titulo, url]) =>
            <li key={titulo}><a className="text-action" href={url} target="_blank" rel="noreferrer">{titulo}</a></li>
          )}</ul>
          {ficha.versiones.length > 0 && <ul>{ficha.versiones.map((version) => <li key={version.id}>
            Versión {version.version} · {version.estado_label} · {fecha(version.verificado_en ?? version.calculado_en)}
          </li>)}</ul>}
        </div>
      </details>
    </>}
  </section>
}
