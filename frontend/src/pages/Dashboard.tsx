import { Carga, Icon, Plazo, RecursoError, SinDatos, Titulo } from '../components/Ui'
import type { IconName } from '../components/Ui'
import { useRecurso } from '../services/api'
import type { Catalogos, DashboardData } from '../services/api'
import { fecha } from '../services/formato'
import { ResumenAlertas } from './Alertas'

const indicators: Array<{
  key: 'total' | 'activos' | 'pendientes_liquidacion' | 'vencidos'; title: string; icon: IconName; color: string
}> = [
  { key: 'total', title: 'Total de contratos', icon: 'folder', color: 'blue' },
  { key: 'activos', title: 'Contratos activos', icon: 'activity', color: 'teal' },
  { key: 'pendientes_liquidacion', title: 'Pendientes de liquidación', icon: 'file', color: 'purple' },
  { key: 'vencidos', title: 'Vencidos operativos', icon: 'clock', color: 'amber' },
]

export function Dashboard() {
  const dashboard = useRecurso<DashboardData>('dashboard/')
  const catalogos = useRecurso<Catalogos>('catalogos/')
  const data = dashboard.data
  return <>
    <Titulo etiqueta="Gestión contractual" title="Panel de control"
      description="Estado de la cartera contractual y sus plazos de seguimiento."
      actions={<>
        <a className="button button-secondary" href="#/contratos"><Icon name="folder" size={18} /> Ver contratos</a>
        {catalogos.data?.entidad && catalogos.data.permisos?.crear &&
          <a className="button button-primary" href="#/contratos/nuevo"><Icon name="plus" size={18} /> Nuevo contrato</a>}
      </>} />
    {dashboard.loading && <Carga texto="Consultando el panel…" />}
    {dashboard.error && <RecursoError error={dashboard.error} recargar={dashboard.recargar} />}
    {data && <>
      <div className="metrics-grid">
        {indicators.map(({ key, title, icon, color }) => <article className="metric panel" key={key}>
          <div className={`metric-icon tint-${color}`}><Icon name={icon} size={19} /></div>
          <p className="metric-label">{title}</p><strong className="metric-value">{data[key].toLocaleString('es-CO')}</strong>
          {data.definiciones[key] && <p className="metric-description">{data.definiciones[key]}</p>}
        </article>)}
      </div>
      {data.alertas && <ResumenAlertas datos={data.alertas} puedeRegistrar={data.puede_registrar_alertas === true} />}
      <div className="dashboard-grid">
        <section className="panel dashboard-panel">
          <div className="panel-heading"><span className="heading-icon tint-blue"><Icon name="calendar" size={18} /></span>
            <div><h2>Próximos vencimientos</h2><p>Plazos operativos confirmados</p></div></div>
          {data.proximos_vencimientos.length ? <ul className="dashboard-list">
            {data.proximos_vencimientos.map((contract) => <li key={contract.id}>
              <a href={`#/contratos/${contract.id}`} className="dashboard-row">
                <div className="truncate"><strong>{contract.numero_contrato} · {contract.anio_contractual}</strong>
                  <small>{contract.contratista_nombre}</small></div>
                <Plazo plazo={contract.vencimiento_operativo} /><Icon name="chevron" size={16} />
              </a>
            </li>)}
          </ul> : <SinDatos icon="calendar" title="Sin próximos vencimientos" />}
        </section>
        <section className="panel dashboard-panel">
          <div className="panel-heading"><span className="heading-icon tint-amber"><Icon name="alert" size={18} /></span>
            <div><h2>Requieren atención</h2><p>Expedientes con seguimiento por revisar</p></div></div>
          {data.requieren_atencion.length ? <ul className="dashboard-list">
            {data.requieren_atencion.map((contract) => <li key={contract.id}>
              <a href={`#/contratos/${contract.id}`} className="dashboard-row">
                <div className="truncate"><strong>{contract.numero_contrato} · {contract.anio_contractual}</strong>
                  <small>{contract.razon_atencion || contract.vencimiento_operativo?.advertencia || contract.contratista_nombre}</small>
                </div><Icon name="chevron" size={16} />
              </a>
            </li>)}
          </ul> : <SinDatos icon="check" title="Sin expedientes pendientes de atención" />}
        </section>
        {data.tareas_pendientes && <section className="panel dashboard-panel">
          <div className="panel-heading">
            <span className="heading-icon tint-blue"><Icon name="check" size={18} /></span>
            <div><h2>Tareas pendientes</h2>
              {data.tareas_vencidas ? <p>{data.tareas_vencidas.toLocaleString('es-CO')} {data.tareas_vencidas === 1 ? 'tarea vencida' : 'tareas vencidas'}</p>
                : <p>Actuaciones por realizar</p>}
            </div>
          </div>
          {data.tareas_pendientes.length ? <ul className="dashboard-list">
            {data.tareas_pendientes.map((tarea) => <li key={tarea.id}>
              <a href={`#/contratos/${tarea.contrato_id}?seccion=actuaciones`} className="dashboard-row">
                <div className="truncate"><strong>{tarea.titulo}</strong><small>{tarea.numero_contrato}</small></div>
                <span className="deadline whitespace-nowrap">{tarea.vence ? fecha(tarea.vence) : 'Sin fecha prevista'}</span>
                <Icon name="chevron" size={16} />
              </a>
            </li>)}
          </ul> : <SinDatos icon="check" title="Sin tareas pendientes" />}
        </section>}
      </div>
    </>}
  </>
}
