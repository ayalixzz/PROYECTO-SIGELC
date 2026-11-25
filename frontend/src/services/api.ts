import { useEffect, useState } from 'react'

export interface Usuario { id: number; username: string; nombre: string }
export interface Sesion { usuario: Usuario | null; csrfToken: string }
export interface Entidad { id: number; nombre: string }
export interface Estado extends Entidad { codigo: string }
export interface Catalogos {
  entidad: Entidad | null
  estados: Estado[]
  tipos_contrato: string[]
  modalidades_contratacion: string[]
  permisos?: { crear: boolean }
}
export interface Vencimiento {
  fecha: string | null
  estado: 'confirmado' | 'provisional' | 'requiere_revision'
  advertencia: string | null
  fecha_original?: string | null
  fecha_teorica?: string | null
}

export interface CamposContrato {
  numero_contrato: string
  anio_contractual: number
  objeto_contractual: string
  tipo_contrato: string
  modalidad_contratacion: string
  contratista_nombre: string
  contratista_identificacion: string
  valor_inicial: string
  valor_adicional: string | null
  valor_actual: string
  fecha_suscripcion: string
  fecha_asignacion: string | null
  fecha_inicio: string | null
  fecha_terminacion: string | null
  supervisor_nombre: string
  supervisor_cargo: string
  observaciones: string
}

export interface Contrato extends CamposContrato {
  id: number
  entidad: Entidad
  estado_actual: Estado
  version_registro: number
  vencimiento_operativo: Vencimiento | null
  razon_atencion?: string
}

export interface Etapa {
  id: number
  orden: number
  estado: Estado
  fecha_inicio: string
  fecha_finalizacion: string | null
  plazo_dias_habiles: number | null
  vencimiento_operativo?: Vencimiento | null
}

export interface Evento {
  id: number
  estado_anterior: Estado | null
  estado_nuevo: Estado
  fecha_solicitud: string
  fecha_aprobacion: string | null
  motivo: string
  resultado: string
  observaciones: string
}

export interface Documento {
  id: number
  nombre_original: string
  tipo_documento: string
  descripcion: string
  estado_revision: string
  version: number
  fecha_carga: string
  descarga_url: string | null
}

export interface Expediente extends Contrato {
  seguimiento: Etapa[]
  historial: Evento[]
  documentos: Documento[]
  advertencias: string[]
  puede_editar_fecha_asignacion: boolean
  permisos: { editar: boolean; ver_documentos: boolean; ver_historial: boolean; generar: boolean; iniciar_seguimiento: boolean;
    cargar_documentos?: boolean }
}

export interface Pagina<T> { count: number; next: string | null; previous: string | null; results: T[] }
export interface AlertaContractual {
  id?: number
  clave: string
  contrato_id: number
  numero_contrato: string
  anio_contractual: number
  tipo: 'competencia' | 'operativa'
  fecha_objetivo: string
  titulo: string
  hito: string
  estado: string
  dias_restantes: number | null
  unidad: 'calendario' | 'habiles'
  prioridad: 'critica' | 'alta' | 'media'
  fuente: { tipo: string; id: number; version?: number | null }
}
export interface AvisoRevisionContractual {
  contrato_id: number
  numero_contrato: string
  anio_contractual: number
  tipo: string
  motivo: string
}
export interface UltimoMemorando {
  id: number
  titulo: string
  radicado: string
  fecha: string
  documento_id: number | null
}
export interface AlertasPanel {
  juridicas: AlertaContractual[]
  operativas: AlertaContractual[]
  requieren_revision: AvisoRevisionContractual[]
  totales: { juridicas: number; operativas: number; requieren_revision: number }
  prioridades_temporales: { contrato_id: number; numero_contrato: string; anio_contractual: number; motivo: string; vence: string }[]
}
export interface AlertasExpediente {
  alertas: AlertaContractual[]
  revisiones: AvisoRevisionContractual[]
  historial: (AlertaContractual & { registrada_en: string; activa: boolean })[]
  fecha_competencia: { fecha: string; fuente: AlertaContractual['fuente'] } | null
  prioridad: { nivel: string; origen: string; motivo: string; vence?: string | null }
  ultimo_memorando: UltimoMemorando | null
  puede_registrar: boolean
}
export interface DashboardData {
  total: number
  activos: number
  pendientes_liquidacion: number
  vencidos: number
  proximos_vencimientos: Contrato[]
  requieren_atencion: Contrato[]
  tareas_pendientes?: { id: number; contrato_id: number; numero_contrato: string; titulo: string; vence: string | null }[]
  tareas_vencidas?: number
  alertas?: AlertasPanel
  puede_registrar_alertas?: boolean
  definiciones: Record<string, string>
}

export interface Previsualizacion {
  campos: Array<{ nombre: string; valor: unknown; fuente: string; pendiente: boolean }>
  versiones: Generacion[]
  disponible: boolean
  motivo: string | null
}

export interface Generacion { id: number; version: number; documento: Documento; campos_pendientes: string[] }

export class ErrorApi extends Error {
  readonly status: number
  readonly campos: Record<string, string>

  constructor(status: number, message: string, campos: Record<string, string> = {}) {
    super(message)
    this.name = 'ErrorApi'
    this.status = status
    this.campos = campos
  }
}

let csrfToken = ''

export function actualizarCsrf(sesion: Sesion) { csrfToken = sesion.csrfToken }

function textoError(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(textoError).filter(Boolean).join(' ')
  if (value && typeof value === 'object') return Object.values(value).map(textoError).filter(Boolean).join(' ')
  return ''
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}) {
  const method = options.method ?? 'GET'
  const headers = new Headers({ Accept: 'application/json' })
  const multipart = options.body instanceof FormData
  if (method !== 'GET' && csrfToken) headers.set('X-CSRFToken', csrfToken)
  if (options.body !== undefined && !multipart) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(`/api/${path}`, {
      method,
      credentials: 'same-origin',
      headers,
      body: multipart ? options.body as FormData : options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new Error('No fue posible conectar con SIGELC. Comprueba que el servidor de la demo esté iniciado.')
  }
  const isJson = response.headers.get('content-type')?.includes('application/json')
  const data: unknown = isJson ? await response.json() : undefined
  if (!response.ok || !isJson) {
    if (response.status === 204) return undefined as T
    const object = data && typeof data === 'object' && !Array.isArray(data) ? data : {}
    const fields = Object.fromEntries(Object.entries(object).map(([key, val]) => [key, textoError(val)]))
    const unavailable = response.status >= 500 || (!isJson && response.ok)
    throw new ErrorApi(response.status, unavailable
      ? 'El servidor de la demo no está disponible. Intenta de nuevo en unos momentos.'
      : fields.detail || fields.non_field_errors || fields.error || (response.status === 403
        ? 'La sesión venció o tu cuenta no tiene acceso a esta operación.'
        : response.status === 404 ? 'No se encontró el registro solicitado.' : 'Revisa los datos e intenta de nuevo.'), fields)
  }
  return data as T
}

export function mensaje(error: unknown) {
  return error instanceof Error ? error.message : 'Ocurrió un error al procesar la solicitud.'
}

export function useRecurso<T>(path: string | null) {
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState<{ key: string; data?: T; error?: string }>()
  const key = `${path}:${revision}`
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    api<T>(path, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setState({ key, data }) })
      .catch((error: unknown) => { if (!controller.signal.aborted) setState({ key, error: mensaje(error) }) })
    return () => controller.abort()
  }, [path, key])
  return {
    data: state?.key === key ? state.data : undefined,
    error: state?.key === key ? state.error : undefined,
    loading: path !== null && state?.key !== key,
    recargar: () => setRevision((value) => value + 1),
  }
}
