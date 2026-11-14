/**
 * The timeline (BRW-01, BRW-02, ORG-03): a justified grid, newest first, with
 * month and day headers, a sticky header, a year/month scrubber, and selection
 * by tile, day or shift-range. Rows are windowed with TanStack Virtual; each
 * block's size is the distance to the next, so positions match the layout.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { Outlet, useMatch, useNavigate, useSearchParams } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { count, duration } from '@/lib/format'
import { layoutTimeline, monthAt, type Block, type DayBlock, type RowBlock } from '@/lib/layout'
import { useAlbums, useAssets, useChange, useFolderListing, useLibrary, usePeople, useScanStatus } from '@/lib/queries'
import { selection, useSelection } from '@/lib/selection'
import type { Asset } from '@/lib/types'

const BADGE: Record<Asset['kind'], string> = { jpg: '', heic: 'HEIC', raw: 'RAW', pair: 'RAW+JPG', video: '' }

export function Timeline() {
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  // `?folder=` narrows the timeline to one folder and its subfolders (BRW-03).
  const folder = params.get('folder') ?? ''
  // `?album=` narrows it to one album (ORG-01).
  const albumId = params.get('album') ?? ''
  const navigate = useNavigate()
  const viewerOpen = useMatch('/photo/:id') !== null
  // `?area=` narrows it to a box on the map: a cluster's items (MAP-01).
  const area = params.get('area') ?? ''
  // `?people=` narrows it to items with every one of these people (PPL-03).
  const peopleFilter = params.get('people') ?? ''
  const assets = useAssets(query, folder, albumId, area, peopleFilter)
  const parent = folder.includes('/') ? folder.slice(0, folder.lastIndexOf('/')) : ''
  const folderCard = useFolderListing(parent).data?.folders.find((f) => f.path === folder)
  const album = useAlbums().data?.find((a) => a.id === albumId)
  const everyone = usePeople().data?.people
  const filteredPeople = peopleFilter
    .split(',')
    .filter(Boolean)
    .map((pid) => everyone?.find((p) => p.id === pid)?.name || 'Unnamed')
  const scan = useScanStatus().data
  const library = useLibrary().data
  const { ids: selected, anchor } = useSelection()
  const change = useChange()

  const items = useMemo(() => assets.data ?? [], [assets.data])
  const index = useMemo(() => new Map(items.map((a, i) => [a.id, i])), [items])
  const firstScan = scan?.state === 'scanning' && scan.firstScan

  // ── Geometry ───────────────────────────────────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null)
  const scrubRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 1200, h: 800 })
  const [st, setSt] = useState(0)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setSize((s) => (el.clientWidth && (el.clientWidth !== s.w || el.clientHeight !== s.h) ? { w: el.clientWidth, h: el.clientHeight } : s))
    const ro = new ResizeObserver(update)
    ro.observe(el)
    update()
    return () => ro.disconnect()
  }, [])

  const layout = useMemo(() => layoutTimeline(items, size.w, firstScan), [items, size.w, firstScan])
  const sizes = useMemo(
    () => layout.blocks.map((b, i) => (layout.blocks[i + 1]?.y ?? layout.total) - b.y),
    [layout],
  )

  const virtualizer = useVirtualizer({
    count: layout.blocks.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => sizes[i] ?? 0,
    paddingStart: layout.blocks[0]?.y ?? 0,
    overscan: 6,
  })
  useEffect(() => virtualizer.measure(), [virtualizer, sizes])

  const raf = useRef<number | null>(null)
  const onScroll = () => {
    if (raf.current !== null) return
    raf.current = requestAnimationFrame(() => {
      raf.current = null
      setSt(scrollRef.current?.scrollTop ?? 0)
    })
  }

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0
    setSt(0)
  }, [query])

  // ── Hover, shift and keyboard ──────────────────────────────────────────────
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [hoverDay, setHoverDay] = useState<string | null>(null)
  const [shift, setShift] = useState(false)

  const trashSelection = useCallback(() => {
    const list = [...selection.get().ids]
    if (!list.length) return
    const only = list.length === 1 ? items[index.get(list[0] as string) ?? -1] : undefined
    selection.clear()
    void change(() => api.trash(list), {
      msg: only ? `Moved ${only.name} to Trash` : `Moved ${count(list.length)} items to Trash`,
      detail: 'Kept for 30 days',
    })
  }, [change, index, items])

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setShift(true)
      const tag = (e.target as HTMLElement | null)?.tagName ?? ''
      if (viewerOpen || tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape') selection.clear()
      else if (e.key === 'Delete') trashSelection()
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setShift(false)
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [viewerOpen, trashSelection])

  // ── Selection ──────────────────────────────────────────────────────────────
  const selMode = selected.size > 0
  const anchorIndex = anchor !== null ? index.get(anchor) : undefined
  const hoverIndex = hoverId !== null ? index.get(hoverId) : undefined
  const rangeOn = shift && anchorIndex !== undefined && hoverIndex !== undefined
  const ra = rangeOn ? Math.min(anchorIndex, hoverIndex) : -1
  const rb = rangeOn ? Math.max(anchorIndex, hoverIndex) : -1

  const selectRange = (id: string) => {
    const a = anchorIndex
    const b = index.get(id)
    if (a === undefined || b === undefined) return selection.toggle(id)
    selection.add(items.slice(Math.min(a, b), Math.max(a, b) + 1).map((x) => x.id), id)
  }
  const toggle = (id: string, e: MouseEvent) => (e.shiftKey && anchorIndex !== undefined ? selectRange(id) : selection.toggle(id))
  const open = (id: string) => navigate({ pathname: `/photo/${id}`, search: params.toString() })

  const tileClick = (asset: Asset, e: MouseEvent) => {
    if (e.shiftKey && anchorIndex !== undefined) return selectRange(asset.id)
    if (selMode || e.ctrlKey || e.metaKey) return toggle(asset.id, e)
    open(asset.id)
  }

  // ── Sticky header and scrubber ─────────────────────────────────────────────
  const currentMonth = monthAt(layout, st + 1)
  let currentDay: DayBlock | null = null
  for (const b of layout.blocks) {
    if (b.y > st + 50) break
    if (b.kind === 'day') currentDay = b
    if (b.kind === 'month') currentDay = null
  }
  const maxSt = Math.max(1, layout.total - size.h)
  const years: { label: string; top: number }[] = []
  for (const m of layout.months) {
    if (!years.some((y) => y.label === String(m.year))) years.push({ label: String(m.year), top: Math.min(1, m.y / maxSt) * 100 })
  }

  const [scrubHover, setScrubHover] = useState<number | null>(null)
  const dragging = useRef(false)
  const scrubFrac = (clientY: number) => {
    const r = scrubRef.current?.getBoundingClientRect()
    if (!r) return 0
    return Math.min(1, Math.max(0, (clientY - r.top - 18) / (r.height - 36)))
  }
  const scrubTo = useCallback(
    (clientY: number) => {
      const f = scrubFrac(clientY)
      if (scrollRef.current) scrollRef.current.scrollTop = f * maxSt
      setScrubHover(f)
    },
    [maxSt],
  )
  useEffect(() => {
    const move = (e: globalThis.MouseEvent) => {
      if (dragging.current) scrubTo(e.clientY)
    }
    const up = () => {
      dragging.current = false
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    return () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
  }, [scrubTo])
  const hoverMonth = scrubHover !== null ? monthAt(layout, scrubHover * maxSt + 1) : undefined

  // ── States ─────────────────────────────────────────────────────────────────
  const emptyLibrary = library !== undefined && library.path === null
  const noResults = !emptyLibrary && assets.isSuccess && items.length === 0 && query.trim() !== ''

  // ── Render ─────────────────────────────────────────────────────────────────
  const renderDay = (b: DayBlock) => {
    const all = b.ids.every((id) => selected.has(id))
    return (
      <div
        key={`d${b.y}`}
        className="day-head"
        style={{ top: b.y }}
        onMouseEnter={() => setHoverDay(b.key)}
        onMouseLeave={() => setHoverDay((k) => (k === b.key ? null : k))}
      >
        {(selMode || hoverDay === b.key) && (
          <button
            type="button"
            className={`check${all ? ' on' : ''}`}
            title="Select day"
            onClick={() => (all ? selection.remove(b.ids, b.ids[b.ids.length - 1] ?? null) : selection.add(b.ids, b.ids[b.ids.length - 1] ?? null))}
          >
            {all && <Icon name="check" size={12} stroke={3.5} />}
          </button>
        )}
        <b>{b.label}</b>
        <span className="muted">{b.place}</span>
        <span className="subtle num">{b.count}</span>
      </div>
    )
  }

  const renderRow = (b: RowBlock) =>
    b.tiles.map(({ x, w, asset }) => {
      const sel = selected.has(asset.id)
      const hover = hoverId === asset.id
      const i = index.get(asset.id) ?? -1
      const inRange = rangeOn && i >= ra && i <= rb && !sel
      const inset = sel ? (b.h < 120 ? 6 : 12) : 0
      const badge = asset.kind === 'video' ? duration(asset.durationS) : BADGE[asset.kind]
      const ring = inRange ? 'inset 0 0 0 2px var(--color-accent)' : hover && !sel ? 'inset 0 0 0 2px rgb(255 255 255 / 0.55)' : 'none'
      const showCheck = hover || selMode
      return (
        <div
          key={asset.id}
          className={`tile${sel || inRange ? ' tinted' : ''}${sel ? ' sel' : ''}${showCheck ? ' show-check' : ''}`}
          style={{ left: x, top: b.y, width: w, height: b.h }}
          onClick={(e) => tileClick(asset, e)}
          onMouseEnter={() => setHoverId(asset.id)}
          onMouseLeave={() => setHoverId((h) => (h === asset.id ? null : h))}
        >
          <div className="art" style={{ inset, background: asset.thumb, boxShadow: ring }} />
          <div className="scrim" />
          {showCheck && (
            <div
              className="tile-check"
              onClick={(e) => {
                e.stopPropagation()
                toggle(asset.id, e)
              }}
            >
              <div className={`check${sel ? ' on' : ''}`}>{sel && <Icon name="check" size={12} stroke={3.5} />}</div>
            </div>
          )}
          {(badge || asset.liveUrl) && (
            <div className="badge" style={{ top: 6 + inset, right: 6 + inset }}>
              {asset.kind === 'video' && <Icon name="play" size={9} fill="currentColor" stroke={0} />}
              {asset.liveUrl && <Icon name="live" size={11} stroke={2} />}
              {asset.liveUrl ? (badge ? `LIVE · ${badge}` : 'LIVE') : badge}
            </div>
          )}
          {asset.favourite && (
            <div className="fav" style={{ left: 8 + inset, bottom: 8 + inset }}>
              <Icon name="heart" size={15} fill="currentColor" stroke={0} />
            </div>
          )}
        </div>
      )
    })

  const renderBlock = (b: Block) => {
    if (b.kind === 'month') {
      return (
        <div key={`m${b.y}`} className="month-head" style={{ top: b.y }}>
          <span className="display">{b.label}</span>
          <span className="num">{b.count}</span>
        </div>
      )
    }
    return b.kind === 'day' ? renderDay(b) : renderRow(b)
  }

  return (
    <div className="timeline" data-screen-label="Timeline">
      {folder && (
        <div className="folder-bar">
          <button
            type="button"
            className="pill-btn back"
            onClick={() => navigate(parent ? `/folders?${new URLSearchParams({ path: parent })}` : '/folders')}
          >
            <Icon name="arrow-left" size={16} />
            Folders
          </button>
          <div className="lines">
            <span className="display">{folderCard?.title ?? folder.split('/').pop()}</span>
            <span className="muted num">
              {[folderCard?.span, folderCard ? `${count(folderCard.count)} items` : '', folderCard?.name]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
          <div className="grow" />
          <button
            type="button"
            className="icon-btn"
            title="Show the whole timeline"
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('folder')
              setParams(next)
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {albumId && (
        <div className="folder-bar">
          <button type="button" className="pill-btn back" onClick={() => navigate('/albums')}>
            <Icon name="arrow-left" size={16} />
            Albums
          </button>
          <div className="lines">
            <span className="display">{album?.name ?? 'Album'}</span>
            <span className="muted num">
              {album ? `${count(album.count)} ${album.count === 1 ? 'item' : 'items'} · the files stay where they are` : ''}
            </span>
          </div>
          <div className="grow" />
          <button
            type="button"
            className="icon-btn"
            title="Show the whole timeline"
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('album')
              setParams(next)
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {peopleFilter && (
        <div className="folder-bar">
          <button
            type="button"
            className="pill-btn back"
            onClick={() => navigate(peopleFilter.includes(',') ? '/people' : `/people/${peopleFilter}`)}
          >
            <Icon name="arrow-left" size={16} />
            People
          </button>
          <div className="lines">
            <span className="display">{everyone ? filteredPeople.join(' & ') : 'People'}</span>
            <span className="muted num">
              {assets.data
                ? `${count(assets.data.length)} ${assets.data.length === 1 ? 'item' : 'items'}${filteredPeople.length > 1 ? ' with all of them' : ''}`
                : ''}
            </span>
          </div>
          <div className="grow" />
          <button
            type="button"
            className="icon-btn"
            title="Show the whole timeline"
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('people')
              setParams(next)
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {area && (
        <div className="folder-bar">
          <button type="button" className="pill-btn back" onClick={() => navigate('/map')}>
            <Icon name="arrow-left" size={16} />
            Map
          </button>
          <div className="lines">
            <span className="display">Taken here</span>
            <span className="muted num">
              {assets.data ? `${count(assets.data.length)} ${assets.data.length === 1 ? 'item' : 'items'} in this area of the map` : ''}
            </span>
          </div>
          <div className="grow" />
          <button
            type="button"
            className="icon-btn"
            title="Show the whole timeline"
            onClick={() => {
              const next = new URLSearchParams(params)
              next.delete('area')
              setParams(next)
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {firstScan && scan && (
        <div className="first-scan">
          <Icon name="loader" size={18} className="spin" style={{ color: 'var(--color-accent)' }} />
          <div className="lines">
            <span style={{ fontWeight: 600 }}>First scan in progress</span>
            <span className="muted num" style={{ fontSize: 'var(--text-sm)' }}>
              {`${count(scan.done)} of ${count(scan.total)} files read from ${library?.path ?? 'the library'}. You can browse while it runs.`}
            </span>
          </div>
          <div className="progress">
            <div style={{ width: `${((scan.done / Math.max(1, scan.total)) * 100).toFixed(1)}%` }} />
          </div>
          <span className="pct num">{`${Math.floor((scan.done / Math.max(1, scan.total)) * 100)}%`}</span>
        </div>
      )}

      <div className="tl-stage">
        <div ref={scrollRef} className="tl-scroll" onScroll={onScroll}>
          <div style={{ position: 'relative', height: Math.ceil(layout.total) }}>
            {virtualizer.getVirtualItems().map((v) => {
              const b = layout.blocks[v.index]
              return b ? renderBlock(b) : null
            })}
            {firstScan && (
              <div className="tl-tail" style={{ top: layout.tailY }}>
                <Icon name="loader" size={16} className="spin" />
                Still reading older folders. Photos appear here as they’re found.
              </div>
            )}
          </div>
        </div>

        {st > 30 && currentMonth && (
          <div className="sticky-head">
            <span className="display">{currentMonth.label}</span>
            <span className="muted">{currentDay ? `${currentDay.label}${currentDay.place ? ` · ${currentDay.place}` : ''}` : ''}</span>
          </div>
        )}

        {items.length > 0 && (
          <div
            ref={scrubRef}
            className="scrubber"
            onMouseDown={(e) => {
              e.preventDefault()
              dragging.current = true
              scrubTo(e.clientY)
            }}
            onMouseMove={(e) => {
              if (!dragging.current) setScrubHover(scrubFrac(e.clientY))
            }}
            onMouseLeave={() => {
              if (!dragging.current) setScrubHover(null)
            }}
          >
            <div className="track">
              {layout.months.map((m) => (
                <div key={m.y} className="m-dot" style={{ top: `${(Math.min(1, m.y / maxSt) * 100).toFixed(2)}%` }} />
              ))}
              {years.map((y) => (
                <div key={y.label} className="year" style={{ top: `${y.top.toFixed(2)}%` }}>
                  {y.label}
                </div>
              ))}
              <div className="thumb" style={{ top: `${(Math.min(1, st / maxSt) * 100).toFixed(2)}%` }} />
              {scrubHover !== null && hoverMonth && (
                <div className="hover-line" style={{ top: `${(scrubHover * 100).toFixed(2)}%` }}>
                  <div className="hover-label">{hoverMonth.label}</div>
                </div>
              )}
            </div>
          </div>
        )}

        {emptyLibrary && (
          <div className="empty-state">
            <div>
              <div className="orb accent">
                <Icon name="folder-open" size={30} />
              </div>
              <div className="display">No library folder yet</div>
              <p>
                The Gallery reads one folder on this PC and works with the files where they are. Nothing is imported or
                copied. Choose the folder in Settings and the first scan starts straight away.
              </p>
              <button type="button" className="btn btn-primary" onClick={() => navigate('/settings')}>
                <Icon name="settings" size={16} />
                Open Settings
              </button>
            </div>
          </div>
        )}

        {noResults && (
          <div className="empty-state">
            <div>
              <div className="orb">
                <Icon name="search" size={30} />
              </div>
              <div className="display">Nothing matches “{query.trim()}”</div>
              <p>
                Search looks at place names, file names, cameras and tags. Check the spelling, or try a broader word like a
                town or year.
              </p>
              <button type="button" className="btn btn-secondary" onClick={() => navigate('/')}>
                <Icon name="x" size={15} />
                Clear search
              </button>
            </div>
          </div>
        )}
      </div>

      <Outlet />
    </div>
  )
}
