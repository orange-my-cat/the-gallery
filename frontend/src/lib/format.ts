/** Display formatting. One place, so every screen writes a size or a date the same way. */

export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] as const
export const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as const

export const pad = (n: number, length = 2) => String(n).padStart(length, '0')

/** Thousands separators, Australian style. */
export const count = (n: number) => n.toLocaleString('en-AU')

export const bytes = (b: number) =>
  b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`

export const duration = (s: number) => `${Math.floor(s / 60)}:${pad(Math.floor(s % 60))}`

export const plural = (n: number, one: string, many = `${one}s`) => (n === 1 ? one : many)

/** Parses "YYYY-MM-DDTHH:mm:ss" (or with a space) as local wall-clock time. */
export function parseLocal(value: string): Date {
  const [y = 0, mo = 1, d = 1, h = 0, mi = 0, s = 0] = value.split(/[-T :]/).map(Number)
  return new Date(y, mo - 1, d, h, mi, s)
}

export function toLocalString(date: Date, sep = 'T'): string {
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `${sep}${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

export const dayLabel = (d: Date) => `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`
export const monthLabel = (d: Date) => `${MONTHS[d.getMonth()]} ${d.getFullYear()}`
export const timeLabel = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`

/** 'Just now', '5 min ago', 'Today 09:12', 'Yesterday 18:40', '3 Nov' - for an ISO instant. */
export function sinceLabel(iso: string | null, now: Date = new Date()): string {
  if (!iso) return 'Never scanned'
  const then = new Date(iso)
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} min ago`
  const hm = `${pad(then.getHours())}:${pad(then.getMinutes())}`
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (then.getTime() >= startOfToday) return `Today ${hm}`
  if (then.getTime() >= startOfToday - 86_400_000) return `Yesterday ${hm}`
  return `${then.getDate()} ${(MONTHS[then.getMonth()] ?? '').slice(0, 3)}`
}
