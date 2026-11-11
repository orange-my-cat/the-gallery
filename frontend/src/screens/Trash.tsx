/**
 * Trash (FOP-02): what was deleted, where it came from, and when it goes for
 * good. Items can be restored, or deleted for good now; everything else is
 * emptied automatically after the retention period set in Settings.
 *
 * Not in the Claude Design handoff; built from the timeline's tiles and the
 * design system's dialog. Deleting for good is the only irreversible thing in
 * the app, so it alone asks first.
 */

import { useState } from 'react'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useChange, useTrashListing } from '@/lib/queries'
import type { TrashItem } from '@/lib/types'

const DAY = 86_400_000

function daysLeft(item: TrashItem): number | null {
  return item.purgeAt ? Math.max(0, Math.ceil((new Date(item.purgeAt).getTime() - Date.now()) / DAY)) : null
}

export function Trash() {
  const listing = useTrashListing().data
  const change = useChange()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)

  const items = listing?.items ?? []
  const chosen = items.filter((i) => selected.has(i.id))
  const ids = chosen.map((i) => i.id)
  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const restore = () => {
    setSelected(new Set())
    void change(() => api.restore(ids), { msg: `Restored ${count(ids.length)} ${plural(ids.length, 'item')}`, detail: 'Back where they were' })
  }
  const purge = () => {
    const which = confirm
    setConfirm(null)
    setSelected(new Set())
    void change(() => (which === 'all' ? api.emptyTrash() : api.purge(ids)), {
      msg: which === 'all' ? 'Emptied the Trash' : `Deleted ${count(ids.length)} ${plural(ids.length, 'item')} for good`,
    })
  }

  return (
    <div className="timeline" data-screen-label="Trash">
      <div className="folders">
        <div className="folders-head">
          <span className="display">Trash</span>
          {listing && <span className="num">{`${count(listing.count)} ${plural(listing.count, 'item')}`}</span>}
          <div className="grow" />
          {chosen.length > 0 ? (
            <>
              <span className="muted">{`${count(chosen.length)} selected`}</span>
              <button type="button" className="pill-btn" onClick={restore}>
                <Icon name="history" size={15} />
                Restore
              </button>
              <button type="button" className="pill-btn danger" onClick={() => setConfirm('selected')}>
                <Icon name="trash" size={15} />
                Delete for good
              </button>
            </>
          ) : (
            items.length > 0 && (
              <button type="button" className="pill-btn danger" onClick={() => setConfirm('all')}>
                <Icon name="trash" size={15} />
                Empty Trash
              </button>
            )
          )}
        </div>

        {listing && (
          <p className="muted trash-note">
            {`Deleted items wait here for ${listing.retentionDays} days, then go for good. Until then each can be restored to where it was.`}
          </p>
        )}

        {listing && items.length === 0 && (
          <div className="trash-empty">
            <Icon name="trash" size={30} />
            <span>The trash is empty.</span>
          </div>
        )}

        <div className="trash-grid">
          {items.map((item) => {
            const left = daysLeft(item)
            const on = selected.has(item.id)
            return (
              <button key={item.id} type="button" className={`trash-item${on ? ' on' : ''}`} onClick={() => toggle(item.id)} title={item.folder + item.name}>
                <div className="art" style={{ background: item.thumb }}>
                  <div className={`check${on ? ' on' : ''}`}>{on && <Icon name="check" size={12} stroke={3.5} />}</div>
                </div>
                <span className="name">{item.name}</span>
                <span className="subtle">{left === null ? '' : left === 0 ? 'Goes today' : `${left} ${plural(left, 'day')} left`}</span>
              </button>
            )
          })}
        </div>
      </div>

      {confirm && (
        <div className="dialog-backdrop" onClick={() => setConfirm(null)}>
          <div className="dialog" role="alertdialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="dialog-title">
              {confirm === 'all' ? 'Empty the Trash?' : `Delete ${count(chosen.length)} ${plural(chosen.length, 'item')} for good?`}
            </div>
            <div className="dialog-body">
              {confirm === 'all'
                ? `All ${count(items.length)} ${plural(items.length, 'item')} are deleted from disk. This cannot be undone.`
                : 'They are deleted from disk. This cannot be undone.'}
            </div>
            <div className="dialog-actions">
              <button type="button" className="pill-btn" autoFocus onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary danger-btn" onClick={purge}>
                Delete for good
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
