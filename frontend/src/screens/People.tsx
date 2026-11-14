/**
 * People (PPL-02, PPL-03): everyone whose face was found, named people first.
 *
 * Not in the Claude Design handoff beyond its nav item; the same card grid as
 * Folders and Albums, with round face crops. Cards can be selected to merge or
 * delete several people at once; a card opens the person. Faces that belong to
 * no group yet have a card of their own.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { SameOrNot } from '@/components/SameOrNot'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useChange, usePeople } from '@/lib/queries'
import { TopBarTakeover } from '@/shell/TopBarTakeover'

export function People() {
  const listing = usePeople().data
  const navigate = useNavigate()
  const change = useChange()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const chosen = [...selected]
  const done = () => setSelected(new Set())

  const scan = listing?.scan
  const searching = scan && scan.done < scan.total

  return (
    <div className="timeline" data-screen-label="People">
      <div className="folders">
        <div className="folders-head">
          <span className="display">People</span>
          {listing && <span className="num">{`${count(listing.people.length)} ${plural(listing.people.length, 'person', 'people')}`}</span>}
        </div>

        {chosen.length > 0 && (
          <TopBarTakeover onEscape={done}>
            <div className="sel-count">
              <b>{count(chosen.length)}</b>
              <span>selected</span>
            </div>
            <div className="grow" />
            <button
              type="button"
              className="pill-btn ghost"
              disabled={chosen.length < 2}
              title={chosen.length < 2 ? 'Select two or more people to merge' : 'Merge into one person'}
              onClick={() => void change(() => api.mergePeople(chosen), { msg: `Merged ${chosen.length} people` }).then(done)}
            >
              <Icon name="merge" size={15} />
              Merge
            </button>
            <button
              type="button"
              className="pill-btn ghost danger"
              onClick={() =>
                void change(() => api.deletePeople(chosen), {
                  msg: `Deleted ${chosen.length} ${plural(chosen.length, 'person', 'people')}`,
                  detail: 'Their photos stay; the faces are set aside',
                }).then(done)
              }
            >
              <Icon name="trash" size={15} />
              Delete
            </button>
            <button
              type="button"
              className="pill-btn ghost"
              title="Statues, posters, faces on a screen: set aside, and so are look-alikes found later"
              onClick={() =>
                void change(() => api.notPeople(chosen), {
                  msg: `Marked ${chosen.length === 1 ? 'a group' : `${chosen.length} groups`} as not a person`,
                  detail: 'Faces that look like them are set aside too',
                }).then(done)
              }
            >
              <Icon name="x" size={15} />
              Not a person
            </button>
            <button type="button" className="icon-btn" title="Clear the selection (Esc)" onClick={done}>
              <Icon name="x" size={16} />
            </button>
          </TopBarTakeover>
        )}

        {!chosen.length && <SameOrNot />}

        {listing && listing.people.length === 0 && !searching && (
          <p className="muted">No faces found yet. Photos are searched for faces in the background after each scan.</p>
        )}

        <div className="folder-grid people-grid">
          {listing?.people.map((p) => {
            const on = selected.has(p.id)
            return (
              <div key={p.id} className={`person-card${on ? ' selected' : ''}`}>
                <button
                  type="button"
                  className="face-btn"
                  onClick={() => (chosen.length ? toggle(p.id) : navigate(`/people/${p.id}`))}
                  title={p.name || 'Unnamed person'}
                >
                  <div className="face-round" style={p.coverUrl ? { backgroundImage: `url("${p.coverUrl}")` } : undefined}>
                    {!p.coverUrl && <Icon name="users" size={28} />}
                  </div>
                </button>
                <button
                  type="button"
                  className={`select-dot${on ? ' on' : ''}`}
                  title={on ? 'Deselect' : 'Select'}
                  aria-pressed={on}
                  onClick={() => toggle(p.id)}
                >
                  {on && <Icon name="check" size={12} stroke={3} />}
                </button>
                <div className="meta">
                  <b className={p.name ? '' : 'muted'}>{p.name || 'Unnamed'}</b>
                  <span className="subtle num">{`${count(p.items)} ${plural(p.items, 'photo')}`}</span>
                </div>
              </div>
            )
          })}
          {!!listing?.unassigned && (
            <div className="person-card">
              <button type="button" className="face-btn" onClick={() => navigate('/people/unassigned')} title="Faces in no group">
                <div className="face-round other">
                  <Icon name="users" size={28} />
                </div>
              </button>
              <div className="meta">
                <b className="muted">Not grouped</b>
                <span className="subtle num">{`${count(listing.unassigned)} ${plural(listing.unassigned, 'face')}`}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
