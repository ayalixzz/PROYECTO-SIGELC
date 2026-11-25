import type { FormEvent } from 'react'
import { Carga, EstadoBadge, Icon, Plazo, RecursoError, SinDatos, Titulo } from '../components/Ui'
import { fecha } from '../services/formato'
import { useRecurso } from '../services/api'
import type { Catalogos, Contrato, Pagina } from '../services/api'

function filtrosUrl(query: URLSearchParams) {
  const value = query.toString()
  return `#/contratos${value ? `?${value}` : ''}`
}

function paginar(query: URLSearchParams, page: number) {
  const next = new URLSearchParams(query)
  if (page > 1) next.set('page', String(page))
  else next.delete('page')
  return filtrosUrl(next)
}

export function Contratos({ query = '' }: { query?: string }) {
  const params = new URLSearchParams(query)
  const page = Math.max(1, Number(params.get('page')) || 1)
  const contratos = useRecurso<Pagina<Contrato>>(`contratos/${query ? `?${query}` : ''}`)
  const catalogos = useRecurso<Catalogos>('catalogos/')
  const hayFiltros = ['buscar', 'anio', 'estado'].some((key) => params.has(key))
  const filtrosExportacion = new URLSearchParams(params)
  filtrosExportacion.delete('page')

  function filtrar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const next = new URLSearchParams()
    for (const key of ['buscar', 'anio', 'estado']) {
      const value = String(data.get(key) ?? '').trim()
      if (value) next.set(key, value)
    }
    const route = filtrosUrl(next)
    if (window.location.hash === route) contratos.recargar()
    else window.location.hash = route
  }

  return <>
    <Titulo etiqueta="Cartera contractual" title="Contratos" description="Busca, registra y consulta expedientes."
      actions={<>
        <a className="button button-secondary" href={`/api/contratos/exportar/?${filtrosExportacion}`} download>
          <Icon name="download" size={18} /> Exportar CSV
        </a>
        {catalogos.data?.permisos?.crear && catalogos.data.entidad &&
          <a className="button button-primary" href="#/contratos/nuevo"><Icon name="plus" size={18} /> Nuevo contrato</a>}
      </>} />
    <section className="panel list-panel" aria-label="Listado contractual">
      {catalogos.error && <div className="panel-pad"><RecursoError error={catalogos.error} recargar={catalogos.recargar} /></div>}
      <form className="filter-bar" onSubmit={filtrar} key={params.get('buscar') + ':' + params.get('anio') + ':' + params.get('estado')}>
        <label className="field filter-search"><span>Buscar contratos</span>
          <span className="search-field"><Icon name="search" size={18} />
            <input name="buscar" defaultValue={params.get('buscar') ?? ''} placeholder="Número, contratista u objeto" maxLength={150} />
          </span>
        </label>
        <label className="field filter-year"><span>Vigencia</span>
          <input name="anio" type="number" min="1900" max="9999" defaultValue={params.get('anio') ?? ''} placeholder="Todas" />
        </label>
        {catalogos.data && <label className="field filter-state"><span>Estado</span>
          <select name="estado" defaultValue={params.get('estado') ?? ''}>
            <option value="">Todos los estados</option>
            {catalogos.data.estados.map((item) => <option value={item.codigo} key={item.id}>{item.nombre}</option>)}
          </select>
        </label>}
        <button className="button button-secondary" type="submit"><Icon name="search" size={17} /> Buscar</button>
        {hayFiltros && <a className="text-action clear-filters" href="#/contratos">Limpiar</a>}
      </form>
      {contratos.loading && <Carga texto="Consultando contratos…" />}
      {contratos.error && <div className="panel-pad"><RecursoError error={contratos.error} recargar={contratos.recargar} /></div>}
      {contratos.data && (contratos.data.results.length ?
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Tabla de contratos">
          <table className="data-table">
            <thead><tr><th>Contrato</th><th>Contratista / objeto</th><th>Estado</th><th>Terminación</th><th>Vencimiento operativo</th><th><span className="sr-only">Acciones</span></th></tr></thead>
            <tbody>{contratos.data.results.map((contract) => <tr key={contract.id}>
              <td><a className="cell-link" href={`#/contratos/${contract.id}`}>{contract.numero_contrato}</a>
                <small>{contract.anio_contractual}</small></td>
              <td><div className="cell-object"><strong>{contract.contratista_nombre}</strong>
                <small className="clamp-2">{contract.objeto_contractual}</small></div></td>
              <td><EstadoBadge estado={contract.estado_actual} /></td>
              <td className="whitespace-nowrap">{fecha(contract.fecha_terminacion)}</td>
              <td><Plazo plazo={contract.vencimiento_operativo} /></td>
              <td><a className="icon-button row-link" href={`#/contratos/${contract.id}`}
                aria-label={`Abrir expediente ${contract.numero_contrato}`} title="Abrir expediente"><Icon name="chevron" size={18} /></a></td>
            </tr>)}</tbody>
          </table>
        </div> :
        <SinDatos title={hayFiltros ? 'No hay contratos que coincidan' : 'Aún no hay contratos registrados'}
          text={hayFiltros ? 'Prueba con otro criterio o limpia los filtros.' : 'Los expedientes registrados aparecerán aquí.'}
          action={hayFiltros ? <a className="text-action" href="#/contratos">Limpiar filtros</a> : undefined} />
      )}
      {contratos.data && <div className="pagination">
        <span>{contratos.data.count.toLocaleString('es-CO')} {contratos.data.count === 1 ? 'contrato' : 'contratos'}</span>
        {(contratos.data.next || contratos.data.previous) && <div className="pagination-controls">
          {contratos.data.previous && <a className="button button-secondary button-small" href={paginar(params, page - 1)}>Anterior</a>}
          <span>Página {page}</span>
          {contratos.data.next && <a className="button button-secondary button-small" href={paginar(params, page + 1)}>Siguiente</a>}
        </div>}
      </div>}
    </section>
  </>
}
