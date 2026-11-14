/**
 * One person (PPL-02, PPL-04), or the faces in no group (`/people/unassigned`).
 *
 * Name them; see their photos; merge them into someone else; delete them.
 * Faces can be selected and removed, moved to another person (or a new one),
 * or marked as not a face. "Find more" lists faces that look like them, to
 * add by hand - nothing joins a person without the owner choosing it.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { PersonPicker } from '@/components/PersonPicker'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useChange, usePerson, useSuggestions, useUnassignedFaces } from '@/lib/queries'
import type { FaceRef } from '@/lib/types'
import { TopBarTakeover } from '@/shell/TopBarTakeover'

type Picking = 'move' | 'merge' | null

function FaceGrid({
  faces,
  selected,
  onToggle,
  onOpen,
}: {
  readonly faces: readonly FaceRef[]
  readonly selected: ReadonlySet<string>
  readonly onToggle: (id: string) => void
  readonly onOpen: (face: FaceRef) => void
}) {
  return (
    <div className="face-grid">
      {faces.map((f) => {
        const on = selected.has(f.id)
        return (
          <div key={f.id} className={`face-tile${on ? ' selected' : ''}`}>
            <button
              type="button"
              className="face-img"
              style={{ backgroundImage: `url("${f.url}")` }}
              aria-pressed={on}
              title={on ? 'Deselect' : 'Select'}
              onClick={() => onToggle(f.id)}
            />
            <span className={`select-dot${on ? ' on' : ''}`} aria-hidden>
              {on && <Icon name="check" size={12} stroke={3} />}
            </span>
            <button type="button" className="open" title="Open the photo" onClick={() => onOpen(f)}>
              <Icon name="image" size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

function useSelection() {
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  return { selected, toggle, clear: () => setSelected(new Set()), all: (ids: string[]) => setSelected(new Set(ids)) }
}

export function Person() {
  const { id = '' } = useParams()
  return id === 'unassigned' ? <Unassigned /> : <OnePerson id={id} />
}

function OnePerson({ id }: { readonly id: string }) {
  const navigate = useNavigate()
  const change = useChange()
  const query = usePerson(id)
  const person = query.data
  const faces = useSelection()
  const more = useSelection()
  const [finding, setFinding] = useState(false)
  const suggestions = useSuggestions(id, finding)
  const [name, setName] = useState('')
  const [picking, setPicking] = useState<Picking>(null)
  useEffect(() => setName(person?.name ?? ''), [person?.name])

  if (query.error) {
    return (
      <div className="timeline">
        <div className="folders">
          <button type="button" className="pill-btn back" onClick={() => navigate('/people')}>
            <Icon name="arrow-left" size={16} />
            People
          </button>
          <p className="muted">This person is no longer in the list.</p>
        </div>
      </div>
    )
  }
  if (!person) return <div className="timeline" />

  const label = person.name || 'this person'
  const chosen = [...faces.selected]
  const extra = [...more.selected]
  const saveName = () => {
    if (name.trim() !== person.name) void change(() => api.renamePerson(id, name), { msg: name.trim() ? `Named ${name.trim()}` : 'Name cleared' })
  }
  const openPhoto = (f: FaceRef) => navigate(`/photo/${f.assetId}?people=${id}`)

  return (
    <div className="timeline" data-screen-label="Person">
      <div className="folders">
        <div className="person-head">
          <button type="button" className="pill-btn back" onClick={() => navigate('/people')}>
            <Icon name="arrow-left" size={16} />
            People
          </button>
          <div className="face-round lg" style={person.coverUrl ? { backgroundImage: `url("${person.coverUrl}")` } : undefined} />
          <div className="lines">
            <input
              className="name-input display"
              value={name}
              placeholder="Add a name"
              onChange={(e) => setName(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                if (e.key === 'Escape') setName(person.name)
              }}
              aria-label="Name"
            />
            <span className="muted num">
              {`${count(person.items)} ${plural(person.items, 'photo')} · ${count(person.faces.length)} ${plural(person.faces.length, 'face')}`}
              {!person.confirmed && ' · found by the app; naming or changing it confirms it'}
            </span>
          </div>
          <div className="grow" />
          <div className="person-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigate(`/?people=${id}`)}>
              <Icon name="images" size={15} />
              View photos
            </button>
            <button type="button" className="pill-btn" onClick={() => setPicking('merge')}>
              <Icon name="merge" size={15} />
              Merge into…
            </button>
            <button
              type="button"
              className="pill-btn"
              title="A statue, a poster, a face on a screen: set aside, and so are look-alikes found later"
              onClick={() =>
                void change(() => api.notPeople([id]), {
                  msg: 'Marked as not a person',
                  detail: 'Faces that look like these are set aside too',
                }).then((ok) => ok && navigate('/people'))
              }
            >
              <Icon name="x" size={15} />
              Not a person
            </button>
            <button
              type="button"
              className="pill-btn danger"
              onClick={() =>
                void change(() => api.deletePerson(id), {
                  msg: `Deleted ${person.name || 'the person'}`,
                  detail: 'Their photos stay; the faces are set aside',
                }).then((ok) => ok && navigate('/people'))
              }
            >
              <Icon name="trash" size={15} />
              Delete
            </button>
          </div>
        </div>

        {chosen.length > 0 && (
          <TopBarTakeover onEscape={faces.clear}>
            <div className="sel-count">
              <b>{count(chosen.length)}</b>
              <span>selected</span>
            </div>
            <div className="grow" />
            <button
              type="button"
              className="pill-btn ghost"
              onClick={() => void change(() => api.removeFaces(chosen), { msg: `Removed ${chosen.length} from ${label}`, detail: 'They won’t be suggested for them again' }).then(faces.clear)}
            >
              <Icon name="x" size={15} />
              {`Remove from ${person.name || 'this person'}`}
            </button>
            <button type="button" className="pill-btn ghost" onClick={() => setPicking('move')}>
              <Icon name="users" size={15} />
              Move to…
            </button>
            <button
              type="button"
              className="pill-btn ghost"
              onClick={() => void change(() => api.ignoreFaces(chosen), { msg: `Marked ${chosen.length} as not a face` }).then(faces.clear)}
            >
              Not a face
            </button>
            <button type="button" className="icon-btn" title="Clear the selection (Esc)" onClick={faces.clear}>
              <Icon name="x" size={16} />
            </button>
          </TopBarTakeover>
        )}
        {chosen.length === 0 && extra.length > 0 && (
          <TopBarTakeover onEscape={more.clear}>
            <div className="sel-count">
              <b>{count(extra.length)}</b>
              <span>selected</span>
            </div>
            <div className="grow" />
            <button
            type="button"
            className="pill-btn ghost"
            onClick={() =>
              void change(() => api.assignFaces(extra, id), { msg: `Added ${extra.length} to ${label}` }).then(() => {
                more.clear()
                void suggestions.refetch()
              })
            }
            >
            <Icon name="plus" size={15} />
            {`Add to ${person.name || 'this person'}`}
            </button>
            <button type="button" className="icon-btn" title="Clear the selection (Esc)" onClick={more.clear}>
              <Icon name="x" size={16} />
            </button>
          </TopBarTakeover>
        )}

        <FaceGrid faces={person.faces} selected={faces.selected} onToggle={faces.toggle} onOpen={openPhoto} />

        <section className="suggest">
          <div className="suggest-head">
            <h2>{`Might also be ${person.name || 'them'}`}</h2>
            {!finding && (
              <button type="button" className="pill-btn" onClick={() => setFinding(true)}>
                <Icon name="search" size={15} />
                Find more faces
              </button>
            )}
          </div>
          {finding && suggestions.data && suggestions.data.length === 0 && (
            <p className="muted">No other faces look like them.</p>
          )}
          {finding && suggestions.data && suggestions.data.length > 0 && (
            <>
              <p className="muted">Faces in no group, or in groups you haven’t confirmed, most alike first. Select the ones that are them.</p>
              <FaceGrid faces={suggestions.data} selected={more.selected} onToggle={more.toggle} onOpen={openPhoto} />
            </>
          )}
        </section>
      </div>

      {picking === 'move' && (
        <PersonPicker
          title={`Move ${chosen.length} ${plural(chosen.length, 'face')} to…`}
          exclude={[id]}
          onClose={() => setPicking(null)}
          onPick={(to, toName) => {
            setPicking(null)
            void change(() => api.assignFaces(chosen, to, toName), { msg: `Moved ${chosen.length} to ${toName || 'a new person'}` }).then(faces.clear)
          }}
        />
      )}
      {picking === 'merge' && (
        <PersonPicker
          title={`Merge ${person.name || 'this person'} into…`}
          exclude={[id]}
          allowNew={false}
          onClose={() => setPicking(null)}
          onPick={(to, toName) => {
            setPicking(null)
            if (!to) return
            void change(() => api.mergePeople([id, to], to), { msg: `Merged into ${toName || 'the other person'}` }).then(
              (ok) => ok && navigate(`/people/${to}`),
            )
          }}
        />
      )}
    </div>
  )
}

function Unassigned() {
  const navigate = useNavigate()
  const change = useChange()
  const faces = useUnassignedFaces().data
  const sel = useSelection()
  const [picking, setPicking] = useState(false)
  const chosen = [...sel.selected]

  return (
    <div className="timeline" data-screen-label="Faces not grouped">
      <div className="folders">
        <div className="person-head">
          <button type="button" className="pill-btn back" onClick={() => navigate('/people')}>
            <Icon name="arrow-left" size={16} />
            People
          </button>
          <div className="lines">
            <span className="display">Not grouped</span>
            <span className="muted">Faces seen too few times to form a group. Put the ones you know with a person.</span>
          </div>
        </div>
        <p className="muted face-hint">Select faces to add them to a person, or mark them as not a face.</p>
        {chosen.length > 0 && (
          <TopBarTakeover onEscape={sel.clear}>
            <div className="sel-count">
              <b>{count(chosen.length)}</b>
              <span>selected</span>
            </div>
            <div className="grow" />
            <button type="button" className="pill-btn ghost" onClick={() => setPicking(true)}>
              <Icon name="users" size={15} />
              Add to a person…
            </button>
            <button
              type="button"
              className="pill-btn ghost"
              onClick={() => void change(() => api.ignoreFaces(chosen), { msg: `Marked ${chosen.length} as not a face` }).then(sel.clear)}
            >
              Not a face
            </button>
            <button type="button" className="icon-btn" title="Clear the selection (Esc)" onClick={sel.clear}>
              <Icon name="x" size={16} />
            </button>
          </TopBarTakeover>
        )}
        {faces && faces.length === 0 && <p className="muted">Every face found is in a group.</p>}
        {faces && <FaceGrid faces={faces} selected={sel.selected} onToggle={sel.toggle} onOpen={(f) => navigate(`/photo/${f.assetId}`)} />}
      </div>
      {picking && (
        <PersonPicker
          title={`Add ${chosen.length} ${plural(chosen.length, 'face')} to…`}
          onClose={() => setPicking(false)}
          onPick={(to, toName) => {
            setPicking(false)
            void change(() => api.assignFaces(chosen, to, toName), { msg: `Added ${chosen.length} to ${toName || 'a new person'}` }).then(sel.clear)
          }}
        />
      )}
    </div>
  )
}
