/**
 * Duplicate review (DUP-03 to DUP-05): one group's copies side by side with
 * the proposed keeper, and the merged-metadata table.
 *
 * The server computes the merge preview with the BRD's rules; every click (keep
 * another copy, use another value) asks it again, so what is shown is exactly
 * what the merge will do. A merge is a pending operation the worker carries
 * out; this screen polls it, then moves on to the next group with Undo on offer.
 */

import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api, ApiError, type OperationRef } from '@/lib/api'
import { bytes, count, plural } from '@/lib/format'
import { invalidateLibrary, keys, settled, useChange, useDuplicate } from '@/lib/queries'
import { useToast } from '@/lib/toast'
import type { DuplicateDetail, MergeField } from '@/lib/types'

import { KIND_LABEL } from './Duplicates'

type Choices = Readonly<Partial<Record<MergeField, string>>>

interface Picks {
  readonly id: string
  /** null: the proposed keeper. */
  readonly keeper: string | null
  readonly choices: Choices
  readonly failed: string | null
}

const fresh = (id: string): Picks => ({ id, keeper: null, choices: {}, failed: null })

export function DuplicateReview() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const change = useChange()
  const toast = useToast()

  // The owner's picks belong to one group: another group starts afresh.
  const [picks, setPicks] = useState<Picks>(fresh(id))
  const current = picks.id === id ? picks : fresh(id)
  const { keeper, choices, failed } = current
  const update = (patch: Partial<Picks>) => setPicks({ ...current, ...patch })
  const setFailed = (message: string | null) => setPicks((p) => (p.id === id ? { ...p, failed: message } : { ...fresh(id), failed: message }))
  const [merge, setMerge] = useState<{ opId: string; group: DuplicateDetail } | null>(null)

  const query = useDuplicate(id, keeper, choices as Record<string, string>)
  const g = query.data && query.data.id === id ? query.data : undefined

  const op = useQuery({
    queryKey: keys.operation(merge?.opId ?? ''),
    queryFn: () => api.operation(merge?.opId ?? ''),
    enabled: merge !== null,
    refetchInterval: 400,
  })

  const leave = (next: string | null) => navigate(next ? `/duplicates/${next}` : '/duplicates', { replace: true })

  // A merge ends applied (toast with Undo, on to the next group) or failed
  // (rolled back: the banner says why, and offers Retry).
  useEffect(() => {
    if (!merge || !op.data || op.data.state === 'pending') return
    const { group, opId } = merge
    setMerge(null)
    void invalidateLibrary(client)
    if (op.data.state === 'applied') {
      const n = group.copies.length
      const kept = group.copies.find((c) => c.assetId === group.keeper)
      toast.show({
        msg: `Merged ${n} copies into ${kept?.name ?? 'one'}`,
        detail: `${n - 1} moved to Trash`,
        undo: async () => {
          await api.undo(opId)
          await settled(opId)
          await invalidateLibrary(client)
        },
      })
      leave(group.position.next)
    } else if (op.data.state === 'failed') {
      setFailed(op.data.error ?? 'The merge could not be completed.')
    }
  }, [merge, op.data])

  if (query.error) {
    const gone = query.error instanceof ApiError && query.error.status === 404
    return (
      <div className="review">
        <div className="review-scroll">
          <div className="review-body">
            <Crumbs position={null} onBack={() => navigate('/duplicates')} />
            <p className="muted">{gone ? 'This group is no longer in the queue: the library changed since it was found.' : query.error.message}</p>
          </div>
        </div>
      </div>
    )
  }
  if (!g) return <div className="review" />

  const n = g.copies.length
  const running = merge !== null && merge.group.id === g.id
  const kept = g.copies.find((c) => c.assetId === g.keeper)
  const keeperName = kept?.name ?? ''
  const others = g.copies.filter((c) => c.assetId !== g.keeper).map((c) => c.letter)
  const hadConflicts = g.rows.filter((r) => r.conflict || r.resolved).length
  const maxRes = Math.max(...g.copies.map((c) => c.width * c.height))
  const cols = `150px repeat(${n}, minmax(0,1fr)) minmax(0,1.15fr)`
  const busy = running

  const startMerge = () => {
    setFailed(null)
    void api
      .merge(g.id, g.keeper, g.choices as Record<string, string>)
      .then((ref) => setMerge({ opId: ref.operationId, group: g }))
      .catch((error: unknown) => setFailed(error instanceof Error ? error.message : String(error)))
  }
  const pick = (field: MergeField, assetId: string) => update({ choices: { ...choices, [field]: assetId } })
  /** BRD: "use the keeper's value" for every open conflict at once. */
  const useKeeperValues = () => {
    const index = g.copies.findIndex((c) => c.assetId === g.keeper)
    const next: Partial<Record<MergeField, string>> = { ...choices }
    for (const r of g.rows) if (r.conflict && r.cells[index]?.text) next[r.field] = g.keeper
    update({ choices: next })
  }
  const settle = async (run: () => Promise<OperationRef>, msg: string, detail: string) => {
    const done = await change(run, { msg, detail })
    if (done) leave(g.position.next)
  }

  const subtitle =
    g.kind === 'exact'
      ? `Byte-identical files in ${n === 2 ? 'two places' : `${n} places`}. One copy is kept and the ${n === 2 ? 'other goes' : 'others go'} to Trash.`
      : 'The same picture to the last pixel, with different details written into the files. Their details are combined into the copy that is kept, and the others go to Trash.'

  return (
    <div className="review" data-screen-label="Duplicate review">
      <div className={`review-scroll${busy ? ' busy' : ''}`}>
        <div className="review-body">
          <Crumbs
            position={g.position}
            onBack={() => navigate('/duplicates')}
            onMove={(to) => to && navigate(`/duplicates/${to}`)}
          />

          <div className="review-head">
            <div className="row">
              <h1>{g.title}</h1>
              <span
                className="chip"
                style={{ background: 'var(--color-surface-active)', color: 'var(--color-text)' }}
              >
                <Icon name="copy" size={13} />
                {KIND_LABEL[g.kind]}
              </span>
              {g.mergeable && (
                <span
                  className="chip num"
                  style={g.conflicts ? { background: 'var(--color-warning-subtle)', color: 'var(--color-warning)' } : { background: 'var(--color-success-subtle)', color: 'var(--color-success)' }}
                >
                  <Icon name={g.conflicts ? 'alert' : 'check'} size={13} />
                  {g.conflicts ? `${g.conflicts} ${plural(g.conflicts, 'conflict')} to resolve` : hadConflicts ? 'Conflicts resolved' : 'No conflicts'}
                </span>
              )}
            </div>
            <p>{subtitle}</p>
          </div>

          {failed && (
            <div className="alert-failed" role="alert">
              <Icon name="alert" size={20} />
              <div className="lines">
                <b>The merge did not happen. Every copy is as it was.</b>
                <span>{failed}</span>
              </div>
              <button type="button" className="btn btn-secondary" onClick={startMerge}>
                <Icon name="rotate-ccw" size={15} />
                Retry
              </button>
              <button type="button" className="icon-btn" title="Dismiss" onClick={() => setFailed(null)}>
                <Icon name="x" size={16} />
              </button>
            </div>
          )}

          {g.blocked && (
            <div className="alert-failed" role="status">
              <Icon name="info" size={20} />
              <div className="lines">
                <b>These can’t be merged as they are</b>
                <span>{g.blocked}</span>
              </div>
            </div>
          )}

          <div className="copies" style={{ gridTemplateColumns: `repeat(${Math.min(n, 4)}, minmax(0, 1fr))` }}>
            {g.copies.map((c) => {
              const isKeeper = c.assetId === g.keeper
              const toTrash = !isKeeper
              const ring = isKeeper ? '0 0 0 2px var(--color-accent)' : 'none'
              const status = isKeeper
                ? { bg: 'var(--color-accent)', fg: 'var(--color-text-on-accent)' }
                : toTrash
                  ? { bg: 'var(--color-media-badge)', fg: 'var(--color-text-on-media)' }
                  : { bg: 'var(--color-success)', fg: 'var(--color-surface-sunken)' }
              return (
                <div key={c.assetId} className="copy-card" style={{ boxShadow: ring }}>
                  <div className="well">
                    <img className="art" src={c.thumbUrl} alt={c.name} style={{ opacity: toTrash ? 0.55 : 1 }} />
                    <span className="letter">{c.letter}</span>
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
                      <dd style={{ fontWeight: c.width * c.height === maxRes ? 700 : 400 }}>
                        {c.width && c.height ? `${c.width} × ${c.height}` : 'Unknown'}
                        {c.width > 0 && <span className="subtle" style={{ fontWeight: 400 }}>{`  ${((c.width * c.height) / 1e6).toFixed(1)} MP`}</span>}
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
                            {g.keeper !== g.proposedKeeper
                              ? 'Chosen by you. Merged metadata is written here.'
                              : `${g.keeperReason}. Merged metadata is written here.`}
                          </span>
                        </div>
                      </div>
                    )}
                    {!isKeeper && g.mergeable && (
                      <button
                        type="button"
                        className="pill-btn"
                        disabled={!!c.cannotKeep}
                        title={c.cannotKeep ?? undefined}
                        onClick={() => update({ keeper: c.assetId, choices: {} })}
                      >
                        Keep this copy instead
                      </button>
                    )}
                    {!isKeeper && c.cannotKeep && <span className="subtle small">{c.cannotKeep}</span>}
                  </div>
                </div>
              )
            })}
          </div>

          {g.mergeable && (
            <section className="merged">
              <div className="heading">
                <h2>Merged metadata</h2>
                <span>{`Each value comes from the copy marked with a check. Click any value to use it instead. The result is written into ${kept?.letter ?? ''}.`}</span>
                {g.conflicts > 0 && (
                  <button type="button" className="pill-btn small" onClick={useKeeperValues}>
                    {`Use ${kept?.letter ?? 'the kept copy'}’s values`}
                  </button>
                )}
              </div>
              <div className="mtable-scroll">
                <div className="mtable" style={{ minWidth: 150 + n * 150 + 180 }}>
                  <div className="hrow caps" style={{ gridTemplateColumns: cols }}>
                    <div>Field</div>
                    {g.copies.map((c) => (
                      <div key={c.assetId} className="hcell">
                        <span className="letter-dot">{c.letter}</span>
                        <span>{c.name}</span>
                      </div>
                    ))}
                    <div className="hresult">
                      <Icon name="merge" size={13} />
                      Merged result
                    </div>
                  </div>
                  {g.rows.map((r) => (
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
                      {r.cells.map((cell, i) => {
                        const copy = g.copies[i]
                        const empty = cell.text === null
                        return (
                          <div
                            key={copy?.assetId ?? i}
                            className={`mcell${empty ? ' empty' : ''}${cell.picked ? ' picked' : ''}`}
                            onClick={empty || !copy ? undefined : () => pick(r.field, copy.assetId)}
                          >
                            <span className="radio-dot">{cell.picked && <Icon name="check" size={10} stroke={3.5} />}</span>
                            <span>{cell.text ?? '—'}</span>
                          </div>
                        )
                      })}
                      <div className="mresult">
                        <b className={r.mode === 'unresolved' ? 'warn' : r.mode === 'none' ? 'none' : ''}>
                          {r.mode === 'unresolved' ? 'Choose a value' : (r.merged ?? '—')}
                        </b>
                        <span className="src">
                          {r.sources.map((l) => (
                            <span key={l} className="letter-dot sm">{l}</span>
                          ))}
                          {r.sourceText}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              {(g.favourite || g.albums.length > 0) && (
                <p className="muted small">
                  {[
                    g.favourite ? 'Stays a favourite' : null,
                    g.albums.length ? `Stays in ${g.albums.join(', ')}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              )}
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
              <span>{`Writing merged metadata into ${keeperName}, then moving ${others.length} ${plural(others.length, 'copy', 'copies')} to Trash`}</span>
            </div>
            <div className="progress indeterminate">
              <div />
            </div>
            <div className="grow" />
            <span className="safe">Safe to leave this screen. Everything rolls back if any step fails.</span>
          </>
        )}
        {!running && (
          <>
            <Icon
              name={g.conflicts ? 'alert' : 'info'}
              size={18}
              style={{ color: g.conflicts ? 'var(--color-warning)' : 'var(--color-text-muted)' }}
            />
            <div className="lines">
              <b>
                {!g.mergeable
                  ? 'Nothing can merge here'
                  : g.conflicts
                    ? `Resolve ${g.conflicts} ${plural(g.conflicts, 'conflict')} to merge`
                    : `Keeps ${kept?.letter ?? ''} · ${others.join(' and ')} go${others.length > 1 ? '' : 'es'} to Trash`}
              </b>
              <span>
                {!g.mergeable
                  ? 'Mark them as not duplicates, or sort them out in the timeline.'
                  : g.conflicts
                    ? 'Pick a value in each highlighted row. Nothing changes on disk until you merge.'
                    : `Merged metadata is written into ${keeperName}. Trashed copies stay restorable for ${g.retentionDays} days, and Undo is available right after.`}
              </span>
            </div>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void settle(() => api.dismissDuplicate(g.id), 'Marked as not duplicates', 'This group won’t be suggested again')}
            >
              Not duplicates
            </button>
            <button type="button" className="btn btn-primary" disabled={!g.mergeable || g.conflicts > 0} onClick={startMerge}>
              <Icon name="merge" size={16} />
              {`Merge ${n} copies`}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function Crumbs({
  position,
  onBack,
  onMove,
}: {
  position: DuplicateDetail['position'] | null
  onBack: () => void
  onMove?: (to: string | null) => void
}) {
  return (
    <div className="crumbs">
      <button type="button" className="back" onClick={onBack}>
        <Icon name="arrow-left" size={15} />
        Duplicates
      </button>
      {position && position.index !== null && (
        <>
          <span className="subtle">/</span>
          <span className="num">{`Group ${count(position.index + 1)} of ${count(position.total)}`}</span>
        </>
      )}
      <div className="grow" />
      {position && onMove && (
        <>
          <button type="button" className="icon-btn outline" title="Previous group" disabled={!position.prev} onClick={() => onMove(position.prev)}>
            <Icon name="chevron-left" size={16} />
          </button>
          <button type="button" className="icon-btn outline" title="Next group" disabled={!position.next} onClick={() => onMove(position.next)}>
            <Icon name="chevron-right" size={16} />
          </button>
        </>
      )}
    </div>
  )
}
