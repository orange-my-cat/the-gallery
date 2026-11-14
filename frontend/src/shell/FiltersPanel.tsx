/**
 * The Filters pill's panel (BRW-06). Not drawn in the Claude Design handoff
 * beyond the pill itself; built from the same popover, pill and field styles.
 * Every change applies at once to the timeline's URL. From another screen, the
 * first change opens the timeline with that filter.
 */

import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { count } from '@/lib/format'
import { FILTER_KEYS, MEDIA_LABEL, type FilterKey } from '@/lib/filters'
import { useAlbums, useFacets } from '@/lib/queries'

type Key = FilterKey | 'album'

export function FiltersPanel() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [here, setParams] = useSearchParams()
  const onTimeline = pathname === '/'
  const params = onTimeline ? here : new URLSearchParams()
  const facets = useFacets().data
  const albums = useAlbums().data?.filter((a) => a.id)

  const set = (changes: Partial<Record<Key, string | null>>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    if (onTimeline) setParams(next, { replace: true })
    else navigate(`/?${next}`)
  }
  const get = (key: Key) => params.get(key) ?? ''
  const any = FILTER_KEYS.some((k) => params.get(k)) || !!params.get('album')

  const choice = (key: Key, value: string, label: string) => (
    <button
      key={value || 'any'}
      type="button"
      className={`filter-pill${get(key) === value ? ' on' : ''}`}
      aria-pressed={get(key) === value}
      onClick={() => set({ [key]: value || null })}
    >
      {label}
    </button>
  )

  return (
    <div className="popover filters-panel" role="dialog" aria-label="Filters">
      <section>
        <span className="caps">Date taken</span>
        <div className="fp-dates">
          <label>
            <span className="subtle">From</span>
            <input className="input" type="date" value={get('from')} max={get('to') || undefined} onChange={(e) => set({ from: e.target.value })} />
          </label>
          <label>
            <span className="subtle">To</span>
            <input className="input" type="date" value={get('to')} min={get('from') || undefined} onChange={(e) => set({ to: e.target.value })} />
          </label>
        </div>
      </section>

      <section>
        <span className="caps">Type</span>
        <div className="fp-choices">
          {choice('type', '', 'All')}
          {Object.entries(MEDIA_LABEL).map(([value, label]) => choice('type', value, label))}
        </div>
      </section>

      <section>
        <span className="caps">Rating</span>
        <div className="fp-choices">
          {choice('rating', '', 'Any')}
          {[1, 2, 3, 4, 5].map((n) => choice('rating', String(n), `${'★'.repeat(n)}${n < 5 ? '+' : ''}`))}
        </div>
      </section>

      <section>
        <span className="caps">Show</span>
        <div className="fp-choices">
          <button
            type="button"
            className={`filter-pill${get('favourites') ? ' on' : ''}`}
            aria-pressed={!!get('favourites')}
            onClick={() => set({ favourites: get('favourites') ? null : 'true' })}
          >
            <Icon name="heart" size={14} />
            Favourites
          </button>
          {choice('located', 'yes', 'Has a location')}
          {choice('located', 'no', 'No location')}
        </div>
      </section>

      <div className="fp-selects">
        <label>
          <span className="caps">Camera</span>
          <select className="input" value={get('camera')} onChange={(e) => set({ camera: e.target.value })}>
            <option value="">Any camera</option>
            {get('camera') && !facets?.cameras.some((c) => c.name === get('camera')) && <option value={get('camera')}>{get('camera')}</option>}
            {facets?.cameras.map((c) => (
              <option key={c.name} value={c.name}>{`${c.name} (${count(c.count)})`}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="caps">Tag</span>
          <select className="input" value={get('tag')} onChange={(e) => set({ tag: e.target.value })}>
            <option value="">Any tag</option>
            {get('tag') && !facets?.tags.some((t) => t.name === get('tag')) && <option value={get('tag')}>{get('tag')}</option>}
            {facets?.tags.map((t) => (
              <option key={t.name} value={t.name}>{`${t.name} (${count(t.count)})`}</option>
            ))}
          </select>
        </label>
        <label>
          <span className="caps">Album</span>
          <select className="input" value={get('album')} onChange={(e) => set({ album: e.target.value })}>
            <option value="">Any album</option>
            {albums?.map((a) => (
              <option key={a.id} value={a.id}>{`${a.name} (${count(a.count)})`}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="fp-foot">
        <button
          type="button"
          className="pill-btn ghost"
          disabled={!any}
          onClick={() => set(Object.fromEntries([...FILTER_KEYS, 'album'].map((k) => [k, null])))}
        >
          Clear all
        </button>
      </div>
    </div>
  )
}
