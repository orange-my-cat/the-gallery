/**
 * Duplicate review (DUP-03 to DUP-06). Copies side by side with the proposed
 * keeper, the merged-metadata table built by the BRD's field rules, and a merge
 * that runs as a pending operation polled until it is applied or rolled back.
 */

import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { bytes, count, plural } from '@/lib/format'
import { invalidateLibrary, keys, useChange, useDuplicates } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { DuplicateGroup, MergeField, MetaValue } from '@/lib/types'

const LETTERS = 'ABCDEF'
const FIELDS: [MergeField, string][] = [
  ['date', 'Capture date'], ['gps', 'GPS'], ['camera', 'Camera'], ['lens', 'Lens'],
  ['keywords', 'Keywords'], ['rating', 'Rating'], ['title', 'Title'], ['description', 'Description'],
]
const STEPS = (keeper: string, others: number) => [
  `Writing merged metadata to ${keeper}`,
  'Verifying the written file',
  `Moving ${others} other ${plural(others, 'copy', 'copies')} to Trash`,
]

const letter = (i: number) => LETTERS[i] ?? '?'

function display(field: MergeField, v: MetaValue): string | null {
  if (v === null || (Array.isArray(v) && v.length === 0)) return null
  if (field === 'keywords' && Array.isArray(v)) return v.join(', ')
  if (field === 'rating' && typeof v === 'number') return '★'.repeat(v) + '☆'.repeat(5 - v)
  return String(v)
}

interface Cell {
  readonly text: string
  readonly empty: boolean
  readonly picked: boolean
}
interface Row {
  readonly field: MergeField
  readonly label: string
  readonly cells: readonly Cell[]
  readonly merged: string
  readonly sources: readonly string[]
  readonly sourceText: string
  readonly conflict: boolean
  readonly resolved: boolean
  readonly mode: 'single' | 'union' | 'none' | 'unresolved'
}

/** The BRD's merge rules, as the preview shows them. The server computes the real merge. */
function computeRows(g: DuplicateGroup, keeper: number, choices: Partial<Record<MergeField, number>>): Row[] {
  const n = g.copies.length
  return FIELDS.map(([field, label]) => {
    const raw = g.meta[field] ?? []
    const vals = raw.map((v) => display(field, v))
    const present = vals.flatMap((v, i) => (v !== null ? [i] : []))
    const distinct = [...new Set(present.map((i) => vals[i]))]
    const isConflict = field !== 'keywords' && distinct.length > 1
    const choice = choices[field]

    let src: number | null = null
    let mode: Row['mode'] = 'single'
    if (choice !== undefined) src = choice
    else if (field === 'keywords' && distinct.length > 1) mode = 'union'
    else if (distinct.length === 1) src = present.includes(keeper) ? keeper : (present[0] ?? null)
    else if (!distinct.length) mode = 'none'
    else mode = 'unresolved'

    let merged: string
    let sources: string[] = []
    let sourceText: string
    if (mode === 'union') {
      merged = [...new Set(present.flatMap((i) => (raw[i] as readonly string[] | null) ?? []))].join(', ')
      sources = present.map(letter)
      sourceText = 'combined'
    } else if (mode === 'none') {
      merged = '—'
      sourceText = 'No copy has a value'
    } else if (mode === 'unresolved') {
      merged = 'Choose a value'
      sourceText = 'Needs your choice'
    } else {
      const s = src ?? 0
      merged = vals[s] ?? '—'
      const allAgree = present.length === n && distinct.length === 1
      sources = choice === undefined && allAgree ? [] : [letter(s)]
      sourceText = choice !== undefined ? (isConflict ? 'your choice' : 'chosen by you') : allAgree ? 'all copies agree' : 'only copy with a value'
    }

    const cells = vals.map((v, i) => ({
      text: v ?? '—',
      empty: v === null,
      picked: mode === 'union' ? present.includes(i) : mode === 'single' && src === i,
    }))
    return { field, label, cells, merged, sources, sourceText, mode, conflict: mode === 'unresolved', resolved: isConflict && mode !== 'unresolved' }
  })
}

export function DuplicateReview() {
  const queue = useDuplicates().data
  const client = useQueryClient()
  const change = useChange()
  const toast = useToast()

  const [gi, setGi] = useState(0)
  const [keepers, setKeepers] = useState<Record<string, number>>({})
  const [choices, setChoices] = useState<Record<string, Partial<Record<MergeField, number>>>>({})
  const [simTrash, setSimTrash] = useState<Record<string, ReadonlySet<number>>>({})
  const [merge, setMerge] = useState<{ opId: string; group: DuplicateGroup; keeper: number } | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  const groups = queue?.groups ?? []
  const g = groups.length ? groups[gi % groups.length] : undefined

  const op = useQuery({
    queryKey: keys.operation(merge?.opId ?? ''),
    queryFn: () => api.operation(merge?.opId ?? ''),
    enabled: merge !== null,
    refetchInterval: 300,
  })

  // A merge ends applied (toast with Undo) or failed (rolled back, banner).
  useEffect(() => {
    if (!merge || !op.data) return
    const { group, keeper, opId } = merge
    const n = group.copies.length
    if (op.data.state === 'applied') {
      setMerge(null)
      void invalidateLibrary(client)
      toast.show({
        msg: `Merged ${n} copies into ${group.copies[keeper]?.name ?? ''}`,
        detail: `${n - 1} moved to Trash`,
        undo: async () => {
          await api.undo(opId)
          await invalidateLibrary(client)
        },
      })
    } else if (op.data.state === 'failed') {
      setMerge(null)
      setFailed(group.id)
    }
  }, [merge, op.data, client, toast])

  const keeper = g ? (keepers[g.id] ?? g.proposedKeeper) : 0
  const rows = useMemo(() => (g && g.kind !== 'similar' ? computeRows(g, keeper, choices[g.id] ?? {}) : []), [g, keeper, choices])

  if (!queue || !g) return <div className="review" />

  const n = g.copies.length
  const isSimilar = g.kind === 'similar'
  const marked = simTrash[g.id] ?? new Set<number>()
  const simN = marked.size
  const conflicts = rows.filter((r) => r.conflict).length
  const hadConflicts = rows.filter((r) => r.conflict || r.resolved).length
  const running = merge?.group.id === g.id
  const step = running ? (op.data?.step ?? 0) : 0
  const keeperName = g.copies[keeper]?.name ?? ''
  const others = g.copies.flatMap((_, i) => (i === keeper ? [] : [letter(i)])).join(' and ')
  const maxRes = Math.max(...g.copies.map((c) => c.width * c.height))
  const cols = `150px repeat(${n}, minmax(0,1fr)) minmax(0,1.15fr)`

  const move = (delta: number) => {
    setGi((i) => (i + delta + groups.length) % groups.length)
    setFailed(null)
  }
  const startMerge = (retry: boolean) => {
    setFailed(null)
    void api.merge(g.id, retry).then(({ operationId }) => setMerge({ opId: operationId, group: g, keeper }))
  }
  const pick = (field: MergeField, i: number) =>
    setChoices((c) => ({ ...c, [g.id]: { ...c[g.id], [field]: i } }))
  const toggleSim = (i: number) =>
    setSimTrash((s) => {
      const next = new Set(s[g.id] ?? [])
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return { ...s, [g.id]: next }
    })

  const typeLabel = g.kind === 'visual' ? 'Visual match' : g.kind === 'exact' ? 'Exact duplicate' : 'Similar · burst'
  const subtitle =
    g.kind === 'visual'
      ? 'The same picture at different sizes and formats, matched by image content. One copy is kept and the others go to Trash.'
      : g.kind === 'exact'
        ? 'Pixel-identical files in two folders. One copy is kept and the other goes to Trash.'
        : `${count(n)} frames from one burst, taken within two seconds. These are separate photos, not copies, so nothing merges. Keep them all or trash the ones you don’t need.`

  return (
    <div className="review" data-screen-label="Duplicate review">
      <div className={`review-scroll${running ? ' busy' : ''}`}>
        <div className="review-body">
          <div className="crumbs">
            <span className="back">
              <Icon name="arrow-left" size={15} />
              Duplicates
            </span>
            <span className="subtle">/</span>
            <span className="num">{`Group ${gi % groups.length + 1} of ${count(queue.total)}`}</span>
            <div className="grow" />
            <button type="button" className="icon-btn outline" title="Previous group" onClick={() => move(-1)}>
              <Icon name="chevron-left" size={16} />
            </button>
            <button type="button" className="icon-btn outline" title="Next group" onClick={() => move(1)}>
              <Icon name="chevron-right" size={16} />
            </button>
          </div>

          <div className="review-head">
            <div className="row">
              <h1>{g.title}</h1>
              <span
                className="chip"
                style={isSimilar ? { background: 'var(--color-success-subtle)', color: 'var(--color-success)' } : { background: 'var(--color-surface-active)', color: 'var(--color-text)' }}
              >
                <Icon name={isSimilar ? 'layers' : 'copy'} size={13} />
                {typeLabel}
              </span>
              {!isSimilar && (
                <span
                  className="chip num"
                  style={conflicts ? { background: 'var(--color-warning-subtle)', color: 'var(--color-warning)' } : { background: 'var(--color-success-subtle)', color: 'var(--color-success)' }}
                >
                  <Icon name={conflicts ? 'alert' : 'check'} size={13} />
                  {conflicts ? `${conflicts} ${plural(conflicts, 'conflict')} to resolve` : hadConflicts ? 'Conflicts resolved' : 'No conflicts'}
                </span>
              )}
            </div>
            <p>{subtitle}</p>
          </div>

          {failed === g.id && (
            <div className="alert-failed" role="alert">
              <Icon name="alert" size={20} />
              <div className="lines">
                <b>Merge failed and was rolled back. No files were changed.</b>
                <span>
                  {`The Gallery couldn’t write metadata to ${keeperName} because the file is read-only. The keeper was restored from its backup and every copy is still in its original folder. Clear the read-only flag in Explorer, then retry.`}
                </span>
              </div>
              <button type="button" className="btn btn-secondary" onClick={() => startMerge(true)}>
                <Icon name="rotate-ccw" size={15} />
                Retry
              </button>
              <button type="button" className="icon-btn" title="Dismiss" onClick={() => setFailed(null)}>
                <Icon name="x" size={16} />
              </button>
            </div>
          )}

          <div className="copies" style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}>
            {g.copies.map((c, i) => {
              const isKeeper = !isSimilar && i === keeper
              const toTrash = isSimilar ? marked.has(i) : !isKeeper
              const ar = c.width / c.height
              const ring = isKeeper ? '0 0 0 2px var(--color-accent)' : isSimilar && toTrash ? '0 0 0 2px var(--color-critical)' : 'none'
              const status = isKeeper
                ? { bg: 'var(--color-accent)', fg: 'var(--color-text-on-accent)' }
                : toTrash
                  ? { bg: 'var(--color-media-badge)', fg: 'var(--color-text-on-media)' }
                  : { bg: 'var(--color-success)', fg: 'var(--color-surface-sunken)' }
              return (
                <div key={c.name} className="copy-card" style={{ boxShadow: ring }}>
                  <div className="well">
                    <div
                      className="art"
                      style={{
                        width: ar >= 1.5 ? '100%' : `${((ar / 1.5) * 100).toFixed(1)}%`,
                        height: ar >= 1.5 ? `${((1.5 / ar) * 100).toFixed(1)}%` : '100%',
                        background: c.thumb,
                        opacity: toTrash ? 0.55 : 1,
                      }}
                    />
                    <span className="letter">{letter(i)}</span>
                    <span className="chip status" style={{ background: status.bg, color: status.fg }}>
                      <Icon name={toTrash ? 'trash' : 'check'} size={13} stroke={2.75} />
                      {toTrash ? 'To Trash' : 'Keep'}
                    </span>
                  </div>
                  <div className="body">
                    <div className="names">
                      <b>{c.name}</b>
                      <span className="mono">{c.path}</span>
                    </div>
                    <dl>
                      <dt>Resolution</dt>
                      <dd style={{ fontWeight: c.width * c.height === maxRes && !isSimilar ? 700 : 400 }}>
                        {`${c.width} × ${c.height}`}
                        <span className="subtle" style={{ fontWeight: 400 }}>{`  ${((c.width * c.height) / 1e6).toFixed(1)} MP`}</span>
                      </dd>
                      <dt>File size</dt>
                      <dd>{bytes(c.size)}</dd>
                      <dt>Format</dt>
                      <dd>{c.format}</dd>
                      <dt>Modified</dt>
                      <dd>{c.modified}</dd>
                    </dl>
                    <div className="grow" />
                    {isKeeper && (
                      <div className="keeper-note">
                        <Icon name="circle-check" size={16} />
                        <div className="lines">
                          <b>Keeping this copy</b>
                          <span className="muted">
                            {keepers[g.id] !== undefined && keepers[g.id] !== g.proposedKeeper
                              ? 'Chosen by you. Merged metadata is written here.'
                              : `${g.keeperReason}. Merged metadata is written here.`}
                          </span>
                        </div>
                      </div>
                    )}
                    {!isSimilar && !isKeeper && (
                      <button type="button" className="pill-btn" onClick={() => setKeepers((k) => ({ ...k, [g.id]: i }))}>
                        Keep this copy instead
                      </button>
                    )}
                    {isSimilar && (
                      <button type="button" className={`pill-btn${toTrash ? '' : ' to-trash'}`} onClick={() => toggleSim(i)}>
                        <Icon name={toTrash ? 'undo' : 'trash'} size={15} />
                        {toTrash ? 'Keep instead' : 'Move to Trash'}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {!isSimilar && (
            <section className="merged">
              <div className="heading">
                <h2>Merged metadata</h2>
                <span>{`Each value comes from the copy marked with a check. Click any value to use it instead. The result is written into ${letter(keeper)}.`}</span>
              </div>
              <div className="mtable">
                <div className="hrow caps" style={{ gridTemplateColumns: cols }}>
                  <div>Field</div>
                  {g.copies.map((c, i) => (
                    <div key={c.name} className="hcell">
                      <span className="letter-dot">{letter(i)}</span>
                      <span>{c.name}</span>
                    </div>
                  ))}
                  <div className="hresult">
                    <Icon name="merge" size={13} />
                    Merged result
                  </div>
                </div>
                {rows.map((r) => (
                  <div key={r.field} className={`mrow${r.conflict ? ' conflict' : ''}`} style={{ gridTemplateColumns: cols }}>
                    <div className="label">
                      <b>{r.label}</b>
                      {r.conflict && (
                        <span className="flag">
                          <Icon name="alert" size={12} />
                          Conflict: pick one
                        </span>
                      )}
                      {r.resolved && (
                        <span className="flag ok">
                          <Icon name="check" size={12} stroke={3} />
                          Resolved
                        </span>
                      )}
                    </div>
                    {r.cells.map((cell, i) => (
                      <div
                        key={i}
                        className={`mcell${cell.empty ? ' empty' : ''}${cell.picked ? ' picked' : ''}`}
                        onClick={cell.empty ? undefined : () => pick(r.field, i)}
                      >
                        <span className="radio-dot">{cell.picked && <Icon name="check" size={10} stroke={3.5} />}</span>
                        <span>{cell.text}</span>
                      </div>
                    ))}
                    <div className="mresult">
                      <b className={r.mode === 'unresolved' ? 'warn' : r.mode === 'none' ? 'none' : ''}>{r.merged}</b>
                      <span className="src">
                        {r.sources.map((l) => <span key={l} className="letter-dot sm">{l}</span>)}
                        {r.sourceText}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>

      <div className="review-foot">
        {running && (
          <>
            <Icon name="loader" size={18} className="spin" style={{ color: 'var(--color-accent)' }} />
            <div className="running">
              <b>Merging…</b>
              <span className="num">{`Step ${step + 1} of 3 · ${STEPS(keeperName, n - 1)[step] ?? ''}`}</span>
            </div>
            <div className="progress">
              <div style={{ width: `${(((step + 1) / 3) * 100).toFixed(0)}%` }} />
            </div>
            <div className="grow" />
            <span className="safe">Safe to leave this screen. Changes roll back if any step fails.</span>
          </>
        )}
        {!running && !isSimilar && (
          <>
            <Icon name={conflicts ? 'alert' : 'info'} size={18} style={{ color: conflicts ? 'var(--color-warning)' : 'var(--color-text-muted)' }} />
            <div className="lines">
              <b>
                {conflicts
                  ? `Resolve ${conflicts} ${plural(conflicts, 'conflict')} to merge`
                  : `Keeps ${letter(keeper)} · ${others} go${n - 1 > 1 ? '' : 'es'} to Trash`}
              </b>
              <span>
                {conflicts
                  ? 'Pick a value in each highlighted row. Nothing changes on disk until you merge.'
                  : `Merged metadata is written into ${keeperName}. Trashed copies stay restorable for 30 days, and Undo is available right after.`}
              </span>
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                void change(() => api.dismissDuplicate(g.id), { msg: 'Marked as not duplicates', detail: 'This group won’t be suggested again' })
              }
            >
              Not duplicates
            </button>
            <button type="button" className="btn btn-primary" disabled={conflicts > 0} onClick={() => startMerge(false)}>
              <Icon name="merge" size={16} />
              {`Merge ${n} copies`}
            </button>
          </>
        )}
        {!running && isSimilar && (
          <>
            <Icon name="layers" size={18} style={{ color: 'var(--color-success)' }} />
            <div className="lines">
              <b>Nothing merges in a similar group</b>
              <span>{simN ? `${simN} of ${n} shots marked for Trash. They stay restorable for 30 days.` : 'All shots are kept. Mark any you no longer need.'}</span>
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void change(() => api.dismissDuplicate(g.id), { msg: `Kept all ${n} shots`, detail: 'Group dismissed' })}
            >
              Keep all
            </button>
            <button
              type="button"
              className="btn-danger-outline"
              disabled={!simN}
              onClick={() => {
                setSimTrash((s) => ({ ...s, [g.id]: new Set() }))
                void change(() => api.trashSimilar(g.id), { msg: `Moved ${simN} ${plural(simN, 'shot')} to Trash`, detail: 'Kept for 30 days' })
              }}
            >
              <Icon name="trash" size={15} />
              {simN ? `Move ${simN} to Trash` : 'Move to Trash'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
