export function BackendStatus({ status }: { status: 'checking' | 'online' | 'offline' }) {
  const message = status === 'checking' ? 'Comprobando conexión' : status === 'online' ? 'Backend disponible' : 'Backend no disponible'
  return <div role="status" className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700"><span className={`h-2.5 w-2.5 rounded-full ${status === 'online' ? 'bg-emerald-500' : status === 'offline' ? 'bg-amber-500' : 'bg-slate-400'}`} />{message}</div>
}
