import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Aviso, Carga, Icon, Marca, RecursoError } from './components/Ui'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Contratos } from './pages/Contratos'
import { ExpedientePage } from './pages/Expediente'
import { ContratoFormPage } from './pages/ContratoForm'
import { SelectorRevision, RevisionInteligente } from './pages/RevisionInteligente'
import { actualizarCsrf, api, mensaje } from './services/api'
import type { Sesion, Usuario } from './services/api'
import './App.css'

function leerRuta() { return window.location.hash.slice(1) || '/' }

function useRuta() {
  const [ruta, setRuta] = useState(leerRuta)
  useEffect(() => {
    const listener = () => setRuta(leerRuta())
    window.addEventListener('hashchange', listener)
    return () => window.removeEventListener('hashchange', listener)
  }, [])
  return ruta
}

function Iniciales({ usuario }: { usuario: Usuario }) {
  const initials = (usuario.nombre || usuario.username).trim().split(/\s+/).slice(0, 2).map((n) => n[0]).join('')
  return <span className="avatar" aria-hidden="true">{initials.toUpperCase()}</span>
}

function Layout({ usuario, seccion, location, cerrar, children }: {
  usuario: Usuario; seccion: 'panel' | 'contratos' | 'revisiones'; location: string; cerrar: () => Promise<void>; children: ReactNode
}) {
  const [menu, setMenu] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function logout() {
    setBusy(true)
    setError('')
    try { await cerrar() } catch (err) { setError(mensaje(err)) } finally { setBusy(false) }
  }
  return <div className="app-layout">
    <a className="skip" href="#contenido" onClick={(e) => { e.preventDefault(); document.getElementById('contenido')?.focus() }}>Ir al contenido</a>
    <aside id="navegacion" className={`sidebar ${menu ? 'sidebar-open' : ''}`}>
      <a className="sidebar-brand" href="#/" onClick={() => setMenu(false)} aria-label="SIGELC, inicio"><Marca /></a>
      <nav className="sidebar-nav" aria-label="Navegación principal">
        <span className="sidebar-label">Área de trabajo</span>
        <a className={seccion === 'panel' ? 'nav-link nav-active' : 'nav-link'} aria-current={seccion === 'panel' ? 'page' : undefined}
          href="#/" onClick={() => setMenu(false)}><Icon name="dashboard" size={18} /> Panel de control</a>
        <a className={seccion === 'contratos' ? 'nav-link nav-active' : 'nav-link'} aria-current={seccion === 'contratos' ? 'page' : undefined}
          href="#/contratos" onClick={() => setMenu(false)}><Icon name="folder" size={19} /> Contratos</a>
        <a className={seccion === 'revisiones' ? 'nav-link nav-active' : 'nav-link'} aria-current={seccion === 'revisiones' ? 'page' : undefined}
          href="#/revisiones" onClick={() => setMenu(false)}><Icon name="search" size={19} /> Revisión inteligente</a>
      </nav>
      <div className="sidebar-footer"><span className="environment-dot" /> Demostración local</div>
    </aside>
    {menu && <button type="button" className="sidebar-overlay" aria-label="Cerrar navegación" onClick={() => setMenu(false)} />}
    <div className="app-main">
      <header className="topbar">
        <div className="topbar-location">
          <button type="button" aria-label="Abrir navegación" aria-expanded={menu} aria-controls="navegacion"
            className="icon-button mobile-menu" onClick={() => setMenu(!menu)}><Icon name="menu" /></button>
          <span>Espacio de trabajo</span><Icon name="chevron" size={14} /><strong>{location}</strong>
        </div>
        <div className="topbar-user">
          <Iniciales usuario={usuario} />
          <span className="topbar-user-name"><strong>{usuario.nombre || usuario.username}</strong><small>Abogado</small></span>
          <button className="icon-button logout" type="button" disabled={busy} title="Cerrar sesión" aria-label="Cerrar sesión" onClick={logout}><Icon name="logout" size={18} /></button>
        </div>
      </header>
      <main id="contenido" className="content" tabIndex={-1}>{error && <Aviso tipo="error">{error}</Aviso>}{children}</main>
    </div>
  </div>
}

export default function App() {
  const ruta = useRuta()
  const [sesion, setSesion] = useState<Sesion>()
  const [error, setError] = useState('')
  const [intento, setIntento] = useState(0)

  function aplicarSesion(value: Sesion) {
    actualizarCsrf(value)
    setSesion(value)
  }

  useEffect(() => {
    const controller = new AbortController()
    api<Sesion>('sesion/', { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) aplicarSesion(value)
    }).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(mensaje(err))
    })
    return () => controller.abort()
  }, [intento])

  if (!sesion) return <main className="gateway">
    {error ? <div className="panel gateway-panel"><Marca small /><RecursoError error={error} recargar={() => { setError(''); setIntento((n) => n + 1) }} /></div>
      : <Carga texto="Comprobando la sesión…" />}
  </main>

  if (!sesion.usuario) return <Login onLogin={aplicarSesion} />

  const [pathname, query] = ruta.split('?')
  const contractRoute = /^\/contratos\/(\d+)(\/editar)?$/.exec(pathname ?? '')
  const reviewRoute = /^\/contratos\/(\d+)\/revisiones(?:\/(\d+))?$/.exec(pathname ?? '')
  let pagina: ReactNode
  let location = 'Panel de control'
  let seccion: 'panel' | 'contratos' | 'revisiones' = 'panel'
  if (pathname === '/revisiones') {
    pagina = <SelectorRevision query={query} />
    location = 'Revisión inteligente'
    seccion = 'revisiones'
  } else if (reviewRoute) {
    const id = Number(reviewRoute[1])
    const revisionId = reviewRoute[2] ? Number(reviewRoute[2]) : undefined
    pagina = <RevisionInteligente key={`${id}:${revisionId ?? ''}`} id={id} revisionId={revisionId} />
    location = 'Revisión inteligente'
    seccion = 'revisiones'
  } else if (pathname === '/contratos/nuevo') {
    pagina = <ContratoFormPage />
    location = 'Nuevo contrato'
    seccion = 'contratos'
  } else if (contractRoute) {
    const id = Number(contractRoute[1])
    pagina = contractRoute[2] ? <ContratoFormPage key={id} id={id} /> : <ExpedientePage key={id} id={id} query={query} />
    location = contractRoute[2] ? 'Editar contrato' : 'Expediente'
    seccion = 'contratos'
  } else if (pathname === '/contratos') {
    pagina = <Contratos query={query} />
    location = 'Contratos'
    seccion = 'contratos'
  } else {
    pagina = <Dashboard />
  }
  async function cerrar() {
    const value = await api<Sesion | undefined>('sesion/', { method: 'DELETE' })
    aplicarSesion(value ?? await api<Sesion>('sesion/'))
    window.location.hash = '/'
  }
  return <Layout usuario={sesion.usuario} seccion={seccion}
    location={location} cerrar={cerrar}>{pagina}</Layout>
}
