import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { describe } from '@/lib/filters'
import { count, plural, sinceLabel } from '@/lib/format'
import { useScanStatus, useStartScan } from '@/lib/queries'
import { useSelection } from '@/lib/selection'
import type { ScanStatus } from '@/lib/types'
import { ActivityPanel } from './ActivityPanel'
import { FiltersPanel } from './FiltersPanel'
import { ScanPanel } from './ScanPanel'
import { SelectionBar } from './SelectionBar'
import { useTopBarTaken } from './TopBarTakeover'

/** The pill, and the details panel it opens (LIB-06). */
function ScanStatusPill() {
  const scan = useScanStatus().data
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!scan) return null
  return (
    <div className="scan-wrap" ref={wrap}>
      <div
        role="button"
        tabIndex={0}
        aria-expanded={open}
        title="Scan details"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((o) => !o)
          }
        }}
      >
        <Pill scan={scan} />
      </div>
      {open && <ScanPanel />}
    </div>
  )
}

/** The Filters pill and its panel (BRW-06): lit, with a count, while any is on. */
function FiltersButton() {
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const on = pathname === '/' ? describe(params).length + (params.get('album') ? 1 : 0) : 0
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="filters-wrap" ref={wrap}>
      <button type="button" className={`pill-btn${on ? ' on' : ''}`} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="filter" size={16} />
        Filters
        {on > 0 && <span className="count-badge num">{on}</span>}
      </button>
      {open && <FiltersPanel />}
    </div>
  )
}

/** The clock button and the operation log it opens (FOP-03). */
function ActivityButton() {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="activity-wrap" ref={wrap}>
      <button type="button" className="icon-btn" title="Activity" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="history" size={17} />
      </button>
      {open && <ActivityPanel />}
    </div>
  )
}

function Pill({ scan }: { readonly scan: ScanStatus }) {
  const busy = scan.state === 'scanning' || scan.pending > 0
  if (busy && !scan.workerOnline) {
    return (
      <div className="scan-status errors clickable">
        <Icon name="alert" size={15} />
        <span style={{ fontWeight: 600 }}>Waiting for the worker</span>
        <span className="muted">Is it running?</span>
      </div>
    )
  }

  if (scan.state === 'scanning') {
    const pct = scan.total ? (scan.done / scan.total) * 100 : 0
    return (
      <div className="scan-status running clickable">
        <Icon name="loader" size={15} className="spin" style={{ color: 'var(--color-accent)' }} />
        <span style={{ fontWeight: 600 }}>{scan.firstScan ? 'First scan' : 'Scanning'}</span>
        <span className="muted num">{`${count(scan.done)} / ${count(scan.total)}`}</span>
        <span className="mini-bar"><span style={{ width: `${pct.toFixed(1)}%` }} /></span>
      </div>
    )
  }
  if (scan.state === 'errors') {
    return (
      <div className="scan-status errors clickable" title={scan.message ?? undefined}>
        <Icon name="alert" size={15} />
        <span className="num" style={{ fontWeight: 600 }}>
          {scan.message ? 'Last scan failed' : `${count(scan.failedFiles)} ${plural(scan.failedFiles, 'file')} couldn’t be read`}
        </span>
        <span className="muted">{sinceLabel(scan.lastScanAt)}</span>
      </div>
    )
  }
  if (scan.state === 'idle' && scan.pending > 0) {
    // The scan has reconciled; thumbnails and previews are still being made.
    return (
      <div className="scan-status running clickable">
        <Icon name="loader" size={15} className="spin" style={{ color: 'var(--color-accent)' }} />
        <span style={{ fontWeight: 600 }}>Making thumbnails</span>
        <span className="muted num">{`${count(scan.pending)} left`}</span>
      </div>
    )
  }
  if (scan.state === 'none') {
    return (
      <div className="scan-status none">
        <span className="dot hollow" />
        No library folder
      </div>
    )
  }
  return (
    <div className="scan-status clickable">
      <span className="dot" />
      <span style={{ fontWeight: 600 }}>Up to date</span>
      <span className="subtle">{sinceLabel(scan.lastScanAt)}</span>
    </div>
  )
}

export function TopBar() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { ids } = useSelection()
  const taken = useTopBarTaken()
  const search = useRef<HTMLInputElement>(null)
  const scan = useScanStatus().data
  const startScan = useStartScan()

  const onTimeline = pathname === '/'
  const query = onTimeline ? (params.get('q') ?? '') : ''
  const scanBusy = !scan || scan.state === 'scanning' || scan.state === 'none'

  // Ctrl+K focuses search from anywhere outside a text field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        search.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (taken) return null // a screen has put its selection controls here
  if (ids.size > 0 && onTimeline) return <SelectionBar />

  const onQuery = (value: string) => {
    if (onTimeline) {
      // Keep the rest of the URL (a folder being browsed), change only the query.
      const next = new URLSearchParams(params)
      if (value) next.set('q', value)
      else next.delete('q')
      setParams(next, { replace: true })
    } else navigate(value ? `/?q=${encodeURIComponent(value)}` : '/')
  }

  return (
    <header className="topbar">
      <label className="search">
        <Icon name="search" size={16} />
        <input
          ref={search}
          className="input"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Search places, files, cameras, tags"
        />
        <span className="kbd">Ctrl K</span>
      </label>
      <FiltersButton />
      <div className="grow" />
      <ScanStatusPill />
      <ActivityButton />
      <button type="button" className="pill-btn" disabled={scanBusy} onClick={() => startScan.mutate()}>
        <Icon name="refresh" size={15} />
        Rescan
      </button>
    </header>
  )
}
