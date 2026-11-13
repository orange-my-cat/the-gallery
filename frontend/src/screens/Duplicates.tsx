/**
 * The duplicate review queue (DUP-01 to DUP-03, DUP-06).
 *
 * Not in the Claude Design handoff, which opens straight into one group; its
 * "Duplicates" breadcrumb implies this list. The same card grid as Folders and
 * Albums: each card is a group, opened into the review screen. Byte-identical
 * groups with nothing to settle can be merged together from here.
 */

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { invalidateLibrary, settled, useDuplicates } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { DuplicateKind } from '@/lib/types'

export const KIND_LABEL: Record<DuplicateKind, string> = {
  exact: 'Exact duplicate',
  pixels: 'Same picture',
}

const FILTERS: readonly [DuplicateKind | 'all', string][] = [
  ['all', 'All'],
  ['exact', 'Exact'],
  ['pixels', 'Same picture'],
]

export function Duplicates() {
  const queue = useDuplicates().data
  const navigate = useNavigate()
  const client = useQueryClient()
  const toast = useToast()
  const [filter, setFilter] = useState<DuplicateKind | 'all'>('all')
  const [merging, setMerging] = useState(false)

  const groups = (queue?.groups ?? []).filter((g) => filter === 'all' || g.kind === filter)
  const ready = queue?.exactReady ?? 0

  /** Each group is its own merge, undoable from Activity; wait for them all. */
  const mergeAll = async () => {
    setMerging(true)
    try {
      const { operations } = await api.mergeExact()
      await invalidateLibrary(client)
      const done = await Promise.all(operations.map((op) => settled(op.id)))
      const failed = done.filter((op) => op.state === 'failed').length
      const merged = done.length - failed
      const msg = `Merged ${count(merged)} exact ${plural(merged, 'group')}`
      if (failed) {
        toast.show({ msg, detail: `${count(failed)} could not merge and ${plural(failed, 'was', 'were')} left as they were`, tone: 'error' })
      } else {
        toast.show({ msg, detail: 'Each can be undone from Activity' })
      }
    } catch (error) {
      toast.show({ msg: 'Nothing was merged', detail: error instanceof Error ? error.message : String(error), tone: 'error' })
    } finally {
      setMerging(false)
      await invalidateLibrary(client)
    }
  }

  return (
    <div className="timeline" data-screen-label="Duplicates">
      <div className="folders">
        <div className="folders-head">
          <span className="display">Duplicates</span>
          {queue && <span className="num">{`${count(queue.total)} ${plural(queue.total, 'group')} to review`}</span>}
        </div>

        {queue && queue.total > 0 && (
          <div className="dup-filters" role="tablist">
            {FILTERS.map(([key, label]) => {
              const n = key === 'all' ? queue.total : queue.counts[key]
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={filter === key}
                  className={`filter-pill${filter === key ? ' on' : ''}`}
                  onClick={() => setFilter(key)}
                  disabled={key !== 'all' && n === 0}
                >
                  {label}
                  <span className="num">{count(n)}</span>
                </button>
              )
            })}
          </div>
        )}

        {ready > 0 && (filter === 'all' || filter === 'exact') && (
          <div className="dup-ready">
            <Icon name="copy" size={18} />
            <div className="lines">
              <b>{`${count(ready)} exact ${plural(ready, 'group')} can merge as proposed`}</b>
              <span>Byte-identical copies with no conflicting details. Each keeps one copy and moves the rest to Trash.</span>
            </div>
            <button type="button" className="btn btn-secondary" disabled={merging} onClick={() => void mergeAll()}>
              {merging ? <Icon name="loader" size={15} className="spin" /> : <Icon name="merge" size={15} />}
              {merging ? 'Merging…' : `Merge ${count(ready)}`}
            </button>
          </div>
        )}

        {queue && queue.total === 0 && (
          <p className="muted">
            Nothing to review. The library is checked for copies after every scan: identical files, and photos that are
            the same picture to the pixel with different details. Photos that only look alike are never offered.
          </p>
        )}

        <div className="folder-grid">
          {groups.map((g) => (
            <div key={g.id} className="folder-card dup-card">
              <button type="button" className="cover-btn" onClick={() => navigate(`/duplicates/${g.id}`)} title={g.title}>
                <div className={`dup-cover n${Math.min(g.thumbs.length, 4)}`}>
                  {g.thumbs.slice(0, 4).map((t) => (
                    <img key={t} src={t} alt="" loading="lazy" />
                  ))}
                </div>
              </button>
              <div className="meta">
                <b>{g.title}</b>
                <span className="subtle">
                  {g.merging ? 'Merging…' : KIND_LABEL[g.kind]}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
