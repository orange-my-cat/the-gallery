/**
 * Choose a person - or name a new one - to move faces to, or to merge into.
 * Typing filters the list; Enter on a name nobody has starts a new person.
 */

import { useMemo, useState } from 'react'

import { Icon } from '@/components/Icon'
import { usePeople } from '@/lib/queries'

interface Props {
  readonly title: string
  /** People not to offer (the one being edited). */
  readonly exclude?: readonly string[]
  /** Offer "New person" (moving faces), or only existing people (merging). */
  readonly allowNew?: boolean
  readonly onPick: (personId: string | null, name: string) => void
  readonly onClose: () => void
}

export function PersonPicker({ title, exclude = [], allowNew = true, onPick, onClose }: Props) {
  const people = usePeople().data?.people ?? []
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const options = useMemo(
    () =>
      people
        .filter((p) => !exclude.includes(p.id))
        .filter((p) => !q || (p.name || 'unnamed').toLowerCase().includes(q))
        .slice(0, 60),
    [people, exclude, q],
  )
  const exact = people.some((p) => p.name.toLowerCase() === q)

  return (
    <div className="picker-backdrop" role="presentation" onClick={onClose}>
      <div className="picker" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="picker-head">
          <b>{title}</b>
          <button type="button" className="icon-btn" title="Close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <input
          className="input"
          autoFocus
          placeholder={allowNew ? 'Find someone, or type a new name' : 'Find someone'}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
            if (e.key === 'Enter') {
              const first = options[0]
              if (allowNew && query.trim() && !exact) onPick(null, query.trim())
              else if (first) onPick(first.id, first.name)
            }
          }}
        />
        <div className="picker-list">
          {allowNew && query.trim() && !exact && (
            <button type="button" className="picker-row new" onClick={() => onPick(null, query.trim())}>
              <span className="face-round sm other">
                <Icon name="plus" size={14} />
              </span>
              {`New person “${query.trim()}”`}
            </button>
          )}
          {allowNew && !query.trim() && (
            <button type="button" className="picker-row new" onClick={() => onPick(null, '')}>
              <span className="face-round sm other">
                <Icon name="plus" size={14} />
              </span>
              New unnamed person
            </button>
          )}
          {options.map((p) => (
            <button key={p.id} type="button" className="picker-row" onClick={() => onPick(p.id, p.name)}>
              <span className="face-round sm" style={p.coverUrl ? { backgroundImage: `url("${p.coverUrl}")` } : undefined} />
              <span className={p.name ? '' : 'muted'}>{p.name || 'Unnamed'}</span>
              <span className="subtle num">{p.items}</span>
            </button>
          ))}
          {!options.length && !allowNew && <p className="muted">Nobody matches.</p>}
        </div>
      </div>
    </div>
  )
}
