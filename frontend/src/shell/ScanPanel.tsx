/**
 * Behind the scan pill (LIB-06). The HLD makes this the health view too: is
 * the worker alive, what is it doing, what could it not read, how did recent
 * scans go. Not in the Claude Design handoff; built from the popover the
 * selection bar uses.
 */

import { Icon } from '@/components/Icon'
import { count, plural, sinceLabel } from '@/lib/format'
import { useRetryFailed, useScanDetails } from '@/lib/queries'
import type { ScanRunSummary } from '@/lib/types'

const KIND_LABEL: Record<string, string> = {
  scan_walk: 'Listing folders',
  index_file: 'Reading files',
  scan_reconcile: 'Checking for removed files',
  thumb_small: 'Grid thumbnails',
  thumb_large: 'Viewer previews',
}

function runLine(run: ScanRunSummary): string {
  if (run.state === 'failed') return run.error ?? 'Failed'
  if (run.state !== 'done') return 'In progress'
  const parts = [`${count(run.seen)} ${plural(run.seen, 'file')}`]
  if (run.new) parts.push(`${count(run.new)} new`)
  if (run.changed) parts.push(`${count(run.changed)} changed`)
  if (run.moved) parts.push(`${count(run.moved)} moved`)
  if (run.missing) parts.push(`${count(run.missing)} missing`)
  return parts.join(' · ')
}

export function ScanPanel() {
  const details = useScanDetails(true).data
  const retry = useRetryFailed()

  if (!details) {
    return (
      <div className="popover scan-panel">
        <span className="muted">Loading…</span>
      </div>
    )
  }

  const queue = Object.entries(details.queue).sort(([a], [b]) => a.localeCompare(b))
  const { worker } = details

  return (
    <div className="popover scan-panel" role="dialog" aria-label="Scan status">
      <section className="sp-worker">
        <span className={`dot${worker.online ? '' : ' off'}`} />
        <div className="lines">
          <b>{worker.online ? 'Worker running' : 'Worker not running'}</b>
          <small className="muted">
            {worker.online
              ? `${worker.processes} ${plural(worker.processes, 'process', 'processes')}`
              : worker.lastSeenAt
                ? `Last heard from ${sinceLabel(worker.lastSeenAt).toLowerCase()}. Scans and thumbnails wait until it is back.`
                : 'Never heard from. Scans and thumbnails wait until it runs.'}
          </small>
          {!worker.online && (
            <small className="subtle">
              Development: <span className="mono">docker compose -f compose.dev.yaml up -d</span>. Production: the{' '}
              <span className="mono">gallery-worker</span> container.
            </small>
          )}
        </div>
      </section>

      <section>
        <div className="caps">Waiting</div>
        {queue.length === 0 ? (
          <span className="muted">Nothing waiting</span>
        ) : (
          <ul className="sp-list">
            {queue.map(([kind, n]) => (
              <li key={kind}>
                <span>{KIND_LABEL[kind] ?? kind}</span>
                <span className="num muted">{count(n)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {details.failedTotal > 0 && (
        <section>
          <div className="sp-head">
            <div className="caps">{`Couldn’t be read · ${count(details.failedTotal)}`}</div>
            <button type="button" className="pill-btn small" disabled={retry.isPending} onClick={() => retry.mutate()}>
              <Icon name="refresh" size={13} />
              Try again
            </button>
          </div>
          <ul className="sp-list failed">
            {details.failed.map((f) => (
              <li key={f.id} title={f.error}>
                <span className="mono path">{f.path || `(${f.kind})`}</span>
                <small className="muted">{`${KIND_LABEL[f.kind] ?? f.kind} · ${f.error.split('\n')[0]}`}</small>
              </li>
            ))}
          </ul>
          {details.failedTotal > details.failed.length && (
            <small className="subtle">{`Showing the latest ${count(details.failed.length)}.`}</small>
          )}
        </section>
      )}

      {details.recent.length > 0 && (
        <section>
          <div className="caps">Recent scans</div>
          <ul className="sp-list runs">
            {details.recent.map((run) => (
              <li key={run.id}>
                <span className="num">{sinceLabel(run.finishedAt ?? run.startedAt)}</span>
                <small className={run.state === 'failed' ? 'critical' : 'muted'}>
                  {`${run.trigger === 'manual' ? 'Rescan' : 'Scheduled'} · ${runLine(run)}`}
                </small>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
