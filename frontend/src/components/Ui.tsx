import type { ReactNode } from 'react'
import type { Estado, Vencimiento } from '../services/api'
import { fecha } from '../services/formato'

const paths = {
  logo: 'M12 2 3 6v6c0 5 4.5 8 9 10 4.5-2 9-5 9-10V6l-9-4Z M8 12l3 3 5-6',
  dashboard: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  folder: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  clock: 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z M12 6v6l4 2',
  calendar: 'M4 5h16a1 1 0 0 1 1 1v14H3V6a1 1 0 0 1 1-1Z M3 10h18 M8 3v4 M16 3v4',
  alert: 'M12 9v4 M12 17h.01 M10.3 4.9 2.4 18a1.5 1.5 0 0 0 1.3 2h16.6a1.5 1.5 0 0 0 1.3-2l-7.9-13.1a2 2 0 0 0-3.4 0Z',
  plus: 'M12 5v14 M5 12h14',
  search: 'M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z M15 15l5 5',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  back: 'M19 12H5 M11 6l-6 6 6 6',
  chevron: 'm9 18 6-6-6-6',
  file: 'M6 3h8l4 4v14H6Z M14 3v5h4 M9 13h6 M9 17h6',
  download: 'M12 3v12 m-4-4 4 4 4-4 M4 16v4h16v-4',
  edit: 'm14 5 5 5 M4 15l11-11a2 2 0 0 1 3 0l2 2a2 2 0 0 1 0 3L9 20H4Z',
  logout: 'M10 4H4v16h6 M10 12h11 m-4-4 4 4-4 4',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  check: 'm5 12 4 4L19 6',
  eye: 'M2 12s3-6 10-6 10 6 10 6-3 6-10 6-10-6-10-6Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  eyeOff: 'm3 3 18 18 M10.5 6.1 12 6c7 0 10 6 10 6a14 14 0 0 1-3 3.2 M6.1 7.1A14 14 0 0 0 2 12s3 6 10 6c1.6 0 3-.3 4.2-.8',
  briefcase: 'M9 7V4h6v3 M4 7h16a1 1 0 0 1 1 1v11H3V8a1 1 0 0 1 1-1Z M3 12h18 M10 12v2h4v-2',
  activity: 'M2 12h5l3-6 4 12 3-6h5',
  collapse: 'M3 5h18v14H3z M9 5v14',
} as const

export type IconName = keyof typeof paths

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    <path d={paths[name]} />
  </svg>
}

export function Marca({ small = false }: { small?: boolean }) {
  return <div className={`marca ${small ? 'marca-sm' : ''}`}>
    <span className="marca-icon"><Icon name="logo" size={small ? 23 : 28} /></span>
    <span><strong>SIGELC</strong><small>Gestión contractual</small></span>
  </div>
}

export function Titulo({ etiqueta, title, description, actions }: {
  etiqueta?: string; title: string; description?: string; actions?: ReactNode
}) {
  return <div className="page-title">
    <div>{etiqueta && <p className="eyebrow">{etiqueta}</p>}<h1>{title}</h1>
      {description && <p className="page-description">{description}</p>}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </div>
}

export function Aviso({ children, tipo = 'aviso' }: { children: ReactNode; tipo?: 'aviso' | 'error' | 'exito' }) {
  return <div className={`notice notice-${tipo}`} role={tipo === 'error' ? 'alert' : 'status'}>
    <Icon name={tipo === 'exito' ? 'check' : 'alert'} size={18} /><div>{children}</div>
  </div>
}

export function Carga({ texto = 'Cargando información…' }: { texto?: string }) {
  return <div className="loading" role="status"><span className="spinner" />{texto}</div>
}

export function SinDatos({ icon = 'folder', title, text, action }: {
  icon?: IconName; title: string; text?: string; action?: ReactNode
}) {
  return <div className="empty"><span className="empty-icon"><Icon name={icon} size={24} /></span>
    <strong>{title}</strong>{text && <p>{text}</p>}{action}</div>
}

export function RecursoError({ error, recargar }: { error: string; recargar: () => void }) {
  return <Aviso tipo="error"><span>{error}</span>
    <button type="button" className="text-action ml-3" onClick={recargar}>Reintentar</button>
  </Aviso>
}

export function EstadoBadge({ estado }: { estado?: Estado | null }) {
  if (!estado) return <span className="subtle">Sin estado</span>
  const tone = estado.codigo.includes('revision') ? 'amber'
    : estado.codigo.includes('requerimiento') ? 'blue' : 'slate'
  return <span className={`badge badge-${tone}`}><span className="badge-dot" />{estado.nombre}</span>
}

export function Plazo({ plazo }: { plazo?: Vencimiento | null }) {
  if (!plazo) return <span className="subtle">Sin plazo</span>
  const pendiente = plazo.estado === 'requiere_revision'
  return <div className="deadline">
    <span>{plazo.fecha ? fecha(plazo.fecha) : pendiente ? 'Por revisar' : '—'}</span>
    {plazo.estado === 'provisional' && <small className="deadline-note">Provisional</small>}
    {pendiente && plazo.fecha && <small className="deadline-note">Requiere revisión</small>}
  </div>
}
