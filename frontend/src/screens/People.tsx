/**
 * People (PPL-02, PPL-03): everyone whose face was found, pinned and then named people first.
 *
 * Not in the Claude Design handoff beyond its nav item; the same card grid as
 * Folders and Albums, with round face crops. Cards can be selected to merge,
 * pin or delete several people at once; a card opens the person. Pinned people
 * have a section of their own at the top, in an order the owner sets by
 * dragging (or Alt+arrow keys). Faces that belong to no group yet have a card
 * of their own.
 */

import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { SameOrNot } from '@/components/SameOrNot'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useChange, usePeople, useRescanPeople } from '@/lib/queries'
import type { PersonSummary } from '@/lib/types'
import { SelectionHead, TopBarTakeover } from '@/shell/TopBarTakeover'

export function People() {
  const listing = usePeople().data
  const navigate = useNavigate()
  const change = useChange()
  const rescan = useRescanPeople()
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
  const pinnedIds = new Set(listing?.people.filter((p) => p.pinned).map((p) => p.id))
  const serverPinned = listing?.people.filter((p) => p.pinned) ?? []
  const others = listing?.people.filter((p) => !p.pinned) ?? []
  // While dragging, and until the new order is saved, the pinned as shown.
  const [preview, setPreview] = useState<readonly PersonSummary[] | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const dropped = useRef(false)
  const pinnedShown = preview ?? serverPinned
  const unpin = chosen.length > 0 && chosen.every((id) => pinnedIds.has(id))
  const pin = (ids: readonly string[], pinned: boolean, name?: string) =>
    change(() => api.pinPeople(ids, pinned), {
      msg: `${pinned ? 'Pinned' : 'Unpinned'} ${name ?? (ids.length === 1 ? 'a person' : `${ids.length} people`)}`,
    })

  const saveOrder = (order: readonly PersonSummary[]) => {
    if (order.map((p) => p.id).join() === serverPinned.map((p) => p.id).join()) return setPreview(null)
    setPreview(order)
    void change(() => api.reorderPinned(order.map((p) => p.id)), { msg: 'Reordered pinned people' }).finally(() =>
      setPreview(null),
    )
  }
  const dragStart = (e: DragEvent, p: PersonSummary) => {
    dropped.current = false
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', p.id)
    setDragging(p.id)
    setPreview(serverPinned)
  }
  const dragOver = (e: DragEvent, target: PersonSummary) => {
    if (!dragging || !preview) return
    e.preventDefault()
    if (target.id === dragging) return
    const box = e.currentTarget.getBoundingClientRect()
    const after = e.clientX > box.left + box.width / 2
    const moved = preview.find((p) => p.id === dragging)
    if (!moved) return
    const rest = preview.filter((p) => p.id !== dragging)
    const at = rest.findIndex((p) => p.id === target.id) + (after ? 1 : 0)
    const next = [...rest.slice(0, at), moved, ...rest.slice(at)]
    if (next.some((p, i) => p.id !== preview[i]?.id)) setPreview(next)
  }
  const dragEnd = () => {
    setDragging(null)
    if (dropped.current && preview) saveOrder(preview)
    else setPreview(null)
  }
  const nudge = (e: KeyboardEvent, p: PersonSummary) => {
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') || preview) return
    e.preventDefault()
    const from = serverPinned.findIndex((q) => q.id === p.id)
    const to = from + (e.key === 'ArrowLeft' ? -1 : 1)
    if (to < 0 || to >= serverPinned.length) return
    const next = [...serverPinned]
    next.splice(from, 1)
    next.splice(to, 0, p)
    saveOrder(next)
  }

  const card = (p: PersonSummary) => {
    const on = selected.has(p.id)
    const movable = p.pinned && !chosen.length && serverPinned.length > 1
    return (
      <div
        key={p.id}
        className={`person-card${on ? ' selected' : ''}${dragging === p.id ? ' dragging' : ''}`}
        draggable={movable}
        onDragStart={movable ? (e) => dragStart(e, p) : undefined}
        onDragOver={movable ? (e) => dragOver(e, p) : undefined}
        onDragEnd={movable ? dragEnd : undefined}
      >
        <button
          type="button"
          className="face-btn"
          onClick={() => (chosen.length ? toggle(p.id) : navigate(`/people/${p.id}`))}
          onKeyDown={movable ? (e) => nudge(e, p) : undefined}
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
        {!chosen.length && (
          <button
            type="button"
            className={`pin-dot${p.pinned ? ' on' : ''}`}
            title={p.pinned ? 'Unpin' : 'Pin to the top'}
            aria-pressed={p.pinned}
            onClick={() => void pin([p.id], !p.pinned, p.name || undefined)}
          >
            <Icon name="pushpin" size={14} />
          </button>
        )}
        <div className="meta">
          <b className={p.name ? '' : 'muted'}>{p.name || 'Unnamed'}</b>
          <span className="subtle num">{`${count(p.items)} ${plural(p.items, 'photo')}`}</span>
        </div>
      </div>
    )
  }

  const scan = listing?.scan
  const searching = scan && scan.done < scan.total

  return (
    <div className="timeline" data-screen-label="People">
      <div className="folders">
        <div className="folders-head">
          <span className="display">People</span>
          {listing && <span className="num">{`${count(listing.people.length)} ${plural(listing.people.length, 'person', 'people')}`}</span>}
          <div className="grow" />
          <button
            type="button"
            className="pill-btn ghost"
            disabled={rescan.isPending}
            title="Look for faces in photos not searched yet, and regroup everyone now instead of at the daily run"
            onClick={() => rescan.mutate()}
          >
            <Icon name="refresh" size={15} />
            Rescan
          </button>
        </div>

        {chosen.length > 0 && (
          <TopBarTakeover onEscape={done}>
            <SelectionHead n={chosen.length} onClear={done} />
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
              className="pill-btn ghost"
              title={unpin ? 'Stop keeping them at the top' : 'Keep them at the top of People'}
              onClick={() => void pin(chosen, !unpin).then(done)}
            >
              <Icon name={unpin ? 'pushpin-off' : 'pushpin'} size={15} />
              {unpin ? 'Unpin' : 'Pin'}
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
          </TopBarTakeover>
        )}

        {!chosen.length && <SameOrNot />}

        {listing && listing.people.length === 0 && !searching && (
          <p className="muted">No faces found yet. Photos are searched for faces in the background after each scan.</p>
        )}

        {pinnedShown.length > 0 && (
          <>
            <div className="people-section caps" title={pinnedShown.length > 1 ? 'Drag to reorder' : undefined}>
              Pinned
            </div>
            <div
              className="folder-grid people-grid"
              onDragOver={(e) => dragging && e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                dropped.current = true
              }}
            >
              {pinnedShown.map(card)}
            </div>
            {(others.length > 0 || !!listing?.unassigned) && <div className="people-section caps">Everyone else</div>}
          </>
        )}

        <div className="folder-grid people-grid">
          {others.map(card)}
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
