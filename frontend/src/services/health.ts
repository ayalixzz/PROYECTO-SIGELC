export async function checkHealth(signal: AbortSignal): Promise<boolean> {
  const response = await fetch('/api/health/', { signal })
  if (!response.ok) return false
  const data: unknown = await response.json()
  return typeof data === 'object' && data !== null && 'status' in data && data.status === 'ok'
}
