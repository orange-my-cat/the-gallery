/**
 * Timeline filters (BRW-06) live in the URL beside `q`, `folder`, `album`,
 * `people` and `area`, so Back, reload and links keep them. These are the
 * newer keys; the album filter is the older `album`.
 */

export const FILTER_KEYS = ['from', 'to', 'type', 'camera', 'tag', 'rating', 'favourites', 'located'] as const
export type FilterKey = (typeof FILTER_KEYS)[number]

export const MEDIA_LABEL: Record<string, string> = { photo: 'Photos', video: 'Videos', raw: 'RAW' }

/** The filters in `params`, as a stable query string (also the cache key). */
export function filtersOf(params: URLSearchParams): string {
  const out = new URLSearchParams()
  for (const key of FILTER_KEYS) {
    const value = params.get(key)
    if (value) out.set(key, value)
  }
  return out.toString()
}

function day(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** One chip per filter that is on: what it shows, and which keys it clears. */
export function describe(params: URLSearchParams): { label: string; keys: FilterKey[] }[] {
  const out: { label: string; keys: FilterKey[] }[] = []
  const from = params.get('from')
  const to = params.get('to')
  if (from && to) out.push({ label: from === to ? day(from) : `${day(from)} – ${day(to)}`, keys: ['from', 'to'] })
  else if (from) out.push({ label: `From ${day(from)}`, keys: ['from'] })
  else if (to) out.push({ label: `Until ${day(to)}`, keys: ['to'] })
  const type = params.get('type')
  if (type) out.push({ label: MEDIA_LABEL[type] ?? type, keys: ['type'] })
  const camera = params.get('camera')
  if (camera) out.push({ label: camera, keys: ['camera'] })
  const tag = params.get('tag')
  if (tag) out.push({ label: `#${tag}`, keys: ['tag'] })
  const rating = Number(params.get('rating') ?? 0)
  if (rating > 0) out.push({ label: `${'★'.repeat(rating)}${rating < 5 ? ' or more' : ''}`, keys: ['rating'] })
  if (params.get('favourites')) out.push({ label: 'Favourites', keys: ['favourites'] })
  const located = params.get('located')
  if (located) out.push({ label: located === 'yes' ? 'Has a location' : 'No location', keys: ['located'] })
  return out
}
