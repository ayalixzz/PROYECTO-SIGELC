const dateOnly = new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeZone: 'UTC' })
const dateTime = new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'America/Bogota' })
const formatInteger = new Intl.NumberFormat('es-CO')
const decimalSeparator = formatInteger.formatToParts(0.1).find((part) => part.type === 'decimal')?.value ?? ','
const calendarioBogota = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Bogota', year: 'numeric', month: '2-digit', day: '2-digit' })

export function hoyBogota() {
  return calendarioBogota.format(new Date())
}

export function fecha(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return (/^\d{4}-\d{2}-\d{2}$/.test(value) ? dateOnly : dateTime).format(date)
}

export function moneda(value?: string) {
  if (!value) return '—'
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value)
  if (!match) return value
  const integer = formatInteger.format(BigInt(match[1]))
  const cents = (match[2] ?? '').padEnd(2, '0')
  return `COP ${integer}${decimalSeparator}${cents}`
}
