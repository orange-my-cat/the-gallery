/**
 * Activity (FOP-03): the operation log behind the clock icon. Every change -
 * edits, moves, trash, albums - with what became of it, and Undo for as long
 * as it can still be undone (until the trash it relies on is emptied).
 *
 * The design has the button and no panel; this is built from the scan panel's
 * popover.
 */

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/api'
import { sinceLabel } from '@/lib/format'
import { invalidateLibrary, settled, useOperations } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { Operation } from '@/lib/types'

const STATE_LABEL: Record<Operation['state'], string> = {
  pending: 'In progress',
  applied: 'Done',
  undoing: 'Undoing',
  undone: 'Undone',
  failed: 'Failed - nothing was changed',
}

export function ActivityPanel() {
  const operations = useOperations(true).data
  const client = useQueryClient()
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)

  const undo = async (op: Operation) => {
    setBusy(op.id)
    try {
      const started = await api.undo(op.id)
      const done = started && (started.state === 'undoing' || started.state === 'pending') ? await settled(op.id) : started
      await invalidateLibrary(client)
      if (done && done.state !== 'undone') toast.show({ msg: 'Undo did not finish', detail: done.error ?? '', tone: 'error' })
      else toast.show({ msg: `Undone: ${op.summary ?? 'change'}` })
    } catch (error) {
      toast.show({ msg: 'Undo failed', detail: error instanceof Error ? error.message : String(error), tone: 'error' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="popover activity" role="dialog" aria-label="Activity">
      <div className="caps" style={{ padding: '4px 8px 6px' }}>Activity</div>
      {!operations && <span className="muted" style={{ padding: 8 }}>Loading…</span>}
      {operations && operations.length === 0 && (
        <span className="muted" style={{ padding: 8 }}>Nothing yet. Every change you make is listed here, and can be undone.</span>
      )}
      {operations?.map((op) => (
        <div key={op.id} className="act-row">
          <span className={`state-dot ${op.state}`} />
          <div className="lines">
            <span>{op.summary}</span>
            <small className={op.state === 'failed' ? 'critical' : 'muted'}>
              {`${sinceLabel(op.createdAt ?? null)} · ${STATE_LABEL[op.state]}`}
              {op.error && op.state === 'failed' ? ` · ${op.error}` : ''}
            </small>
          </div>
          {op.undoable && (
            <button type="button" className="pill-btn small" disabled={busy === op.id} onClick={() => void undo(op)}>
              Undo
            </button>
          )}
        </div>
      ))}
    </div>
  )
}
