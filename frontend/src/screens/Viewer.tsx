/**
 * The viewer (BRW-04, BRW-05, ORG-04): full-size picture with zoom, next and
 * previous, a filmstrip, and an info panel whose title, description, date and
 * place are edited in place and written back as XMP (MET-02).
 *
 * Always dark, in both themes. The picture is the 2,048 px preview laid over
 * the grid thumbnail, so something shows at once. Video plays from the
 * original through the design's own control bar; in demo mode (no video URL)
 * playback is simulated, as in the prototype.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { flushSync } from 'react-dom'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api, type AssetPatch } from '@/lib/api'
import { bytes, count, DAYS, duration, MONTHS, parseLocal, plural, timeLabel } from '@/lib/format'
import { useAssets, useChange } from '@/lib/queries'
import type { Asset } from '@/lib/types'

type Field = 'title' | 'desc' | 'date' | 'place'
const FIELD_LABEL: Record<Field, string> = { title: 'Title', desc: 'Description', date: 'Capture date', place: 'Location' }
const DATE_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/

const SOUND_KEY = 'gallery.volume'
interface Sound {
  readonly volume: number
  readonly muted: boolean
}
function readSound(): Sound {
  try {
    const stored = JSON.parse(localStorage.getItem(SOUND_KEY) ?? 'null') as Partial<Sound> | null
    if (stored && typeof stored.volume === 'number') {
      return { volume: Math.max(0, Math.min(1, stored.volume)), muted: stored.muted === true }
    }
  } catch {
    // Unreadable or blocked: full volume.
  }
  return { volume: 1, muted: false }
}

export function Viewer() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  // The same list the timeline shows, so next and previous stay within a folder or search.
  const items = useAssets(params.get('q') ?? '', params.get('folder') ?? '', params.get('album') ?? '').data ?? []
  const [infoOpen, setInfoOpen] = useState(true)

  const index = items.findIndex((a) => a.id === id)
  const asset = items[index]

  const go = useCallback(
    (target: string | null) => navigate({ pathname: target ? `/photo/${target}` : '/', search: params.toString() }),
    [navigate, params],
  )

  if (!asset) return null
  return (
    <ViewerBody
      key={asset.id}
      asset={asset}
      items={items}
      index={index}
      infoOpen={infoOpen}
      onToggleInfo={() => setInfoOpen((o) => !o)}
      go={go}
    />
  )
}

interface BodyProps {
  readonly asset: Asset
  readonly items: readonly Asset[]
  readonly index: number
  readonly infoOpen: boolean
  readonly onToggleInfo: () => void
  readonly go: (id: string | null) => void
}

function ViewerBody({ asset, items, index, infoOpen, onToggleInfo, go }: BodyProps) {
  const change = useChange()
  const stageRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [stage, setStage] = useState({ w: 1000, h: 700 })
  const [zoom, setZoom] = useState(1)
  const [playing, setPlaying] = useState(false)
  const [vt, setVt] = useState(0)
  const liveRef = useRef<HTMLVideoElement>(null)
  const [liveOn, setLiveOn] = useState(false)
  // Hovering plays the motion silently and stops when the pointer leaves; a
  // click or Space plays it through, with sound - the browser only allows
  // sound after the viewer has done something.
  const liveMode = useRef<'hover' | 'full'>('hover')
  const [editing, setEditing] = useState<{ field: Field; value: string; orig: string; error: boolean } | null>(null)

  const prev = items[index - 1]
  const next = items[index + 1]
  const isVideo = asset.kind === 'video'
  const isRaw = asset.kind === 'raw' || asset.kind === 'pair'
  const ar = asset.width / asset.height

  useLayoutEffect(() => {
    const el = stageRef.current
    if (!el) return
    const update = () => setStage((s) => (el.clientWidth && (el.clientWidth !== s.w || el.clientHeight !== s.h) ? { w: el.clientWidth, h: el.clientHeight } : s))
    const ro = new ResizeObserver(update)
    ro.observe(el)
    update()
    return () => ro.disconnect()
  }, [])

  // Simulated playback, ticking as the prototype does - demo mode only.
  useEffect(() => {
    if (!playing || asset.videoUrl) return
    const t = window.setInterval(() => {
      setVt((v) => {
        if (v + 0.25 >= asset.durationS) {
          setPlaying(false)
          return asset.durationS
        }
        return v + 0.25
      })
    }, 250)
    return () => window.clearInterval(t)
  }, [playing, asset.durationS, asset.videoUrl])

  const fitW = Math.min(Math.max(200, stage.w - 176), Math.max(200, stage.h - 48) * ar)
  const fitH = fitW / ar
  const pct = Math.round(((fitW * zoom) / asset.width) * 100)
  const maxZoom = Math.max(1, (asset.width * 2) / fitW)

  // ── Panning: a zoomed picture is dragged around, never past its own edges ──
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const clampPan = (p: { x: number; y: number }, z: number) => {
    const mx = Math.max(0, (fitW * z - stage.w) / 2)
    const my = Math.max(0, (fitH * z - stage.h) / 2)
    return { x: Math.max(-mx, Math.min(mx, p.x)), y: Math.max(-my, Math.min(my, p.y)) }
  }
  const shown = clampPan(pan, zoom) // clamped here too, so a resized window never strands it
  // Zoom keeping the point (cx, cy), measured from the stage's centre, where it is.
  const zoomTo = (target: number, cx = 0, cy = 0) => {
    const z = Math.max(1, Math.min(maxZoom, target))
    setPan(clampPan({ x: cx - ((cx - shown.x) * z) / zoom, y: cy - ((cy - shown.y) * z) / zoom }, z))
    setZoom(z)
  }
  const zoomIn = () => zoomTo(zoom * 2)
  const zoomOut = () => zoomTo(zoom / 2)

  // ── Wheel zoom, around the pointer ─────────────────────────────────────────
  // A native listener: React's wheel handlers are passive and can't stop the page scrolling.
  const onWheel = useRef<(e: WheelEvent) => void>(() => {})
  onWheel.current = (e: WheelEvent) => {
    if ((e.target as HTMLElement | null)?.closest('.video-bar')) return // its volume slider has its own
    e.preventDefault()
    const box = stageRef.current?.getBoundingClientRect()
    if (!box) return
    const lines = e.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 : e.deltaMode === WheelEvent.DOM_DELTA_PAGE ? box.height : 1
    const step = Math.max(-100, Math.min(100, e.deltaY * lines)) // one notch is ~100 px in most browsers
    // Rendered at once, so the next wheel event in the same frame builds on this zoom, not the last.
    flushSync(() => zoomTo(zoom * Math.exp(-step * 0.002), e.clientX - (box.left + box.width / 2), e.clientY - (box.top + box.height / 2)))
  }
  useEffect(() => {
    const el = stageRef.current
    if (!el) return
    const handle = (e: WheelEvent) => onWheel.current(e)
    el.addEventListener('wheel', handle, { passive: false })
    return () => el.removeEventListener('wheel', handle)
  }, [])

  const drag = useRef<{ id: number; x: number; y: number; from: { x: number; y: number }; moved: boolean } | null>(null)
  const panHandlers = {
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      if (zoom <= 1 || e.button !== 0) return
      e.currentTarget.setPointerCapture(e.pointerId)
      drag.current = { id: e.pointerId, x: e.clientX, y: e.clientY, from: shown, moved: false }
      setDragging(true)
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = drag.current
      if (!d || d.id !== e.pointerId) return
      const dx = e.clientX - d.x
      const dy = e.clientY - d.y
      if (!d.moved && Math.hypot(dx, dy) < 4) return
      d.moved = true
      setPan(clampPan({ x: d.from.x + dx, y: d.from.y + dy }, zoom))
    },
    onPointerUp: () => setDragging(false),
    onPointerCancel: () => {
      drag.current = null
      setDragging(false)
    },
    // A drag that ends over a video must not also play or pause it.
    onClickCapture: (e: React.MouseEvent) => {
      if (drag.current?.moved) e.stopPropagation()
      drag.current = null
    },
    onDoubleClick: (e: React.MouseEvent<HTMLDivElement>) => {
      const box = stageRef.current?.getBoundingClientRect()
      const cx = box ? e.clientX - (box.left + box.width / 2) : 0
      const cy = box ? e.clientY - (box.top + box.height / 2) : 0
      if (zoom > 1) zoomTo(1)
      else zoomTo(Math.min(asset.width / fitW, maxZoom), cx, cy)
    },
  }

  const toggleFav = useCallback(() => {
    const was = asset.favourite
    void change(() => api.updateAssets([asset.id], { favourite: !was }), {
      msg: was ? 'Removed from Favourites' : 'Added to Favourites',
      detail: asset.name,
    })
  }, [asset, change])

  const trash = useCallback(() => {
    const after = next ?? prev
    void change(() => api.trash([asset.id]), { msg: `Moved ${asset.name} to Trash`, detail: 'Kept for 30 days' })
    go(after ? after.id : null)
  }, [asset, change, go, next, prev])

  const togglePlay = () => {
    const video = videoRef.current
    if (video) {
      if (video.paused) void video.play().catch(() => setPlaying(false))
      else video.pause()
      return
    }
    setVt((v) => (v >= asset.durationS ? 0 : v))
    setPlaying((p) => !p)
  }

  // ── Live Photo: the motion clip, over the still ────────────────────────────
  const playLive = (full: boolean) => {
    const clip = liveRef.current
    if (!clip) return
    if (liveOn && liveMode.current === 'full' && !full) return // already playing through
    liveMode.current = full ? 'full' : 'hover'
    clip.muted = !full || sound.muted
    clip.volume = sound.volume
    clip.currentTime = 0
    void clip.play().then(() => setLiveOn(true), () => setLiveOn(false))
  }
  const stopLive = () => {
    liveRef.current?.pause()
    setLiveOn(false)
  }

  // ── Seeking: click or drag anywhere along the bar ──────────────────────────
  const scrubbing = useRef(false)
  const seekTo = (e: React.PointerEvent<HTMLDivElement>) => {
    const video = videoRef.current
    const length = (video && Number.isFinite(video.duration) ? video.duration : 0) || asset.durationS
    if (!length) return
    const box = e.currentTarget.getBoundingClientRect()
    const t = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * length
    if (video) video.currentTime = t
    setVt(t)
  }
  const seekHandlers = {
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      scrubbing.current = true
      e.currentTarget.setPointerCapture(e.pointerId)
      seekTo(e)
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      if (scrubbing.current) seekTo(e)
    },
    onPointerUp: () => {
      scrubbing.current = false
    },
    onPointerCancel: () => {
      scrubbing.current = false
    },
  }

  // ── Volume: remembered in this browser across videos and visits ────────────
  const [sound, setSound] = useState(readSound)
  useEffect(() => {
    const video = videoRef.current
    if (video) {
      video.volume = sound.volume
      video.muted = sound.muted
    }
    try {
      localStorage.setItem(SOUND_KEY, JSON.stringify(sound))
    } catch {
      // Private window or blocked storage: the setting just isn't remembered.
    }
  }, [sound])
  const setVolume = (volume: number) => setSound({ volume: Math.max(0, Math.min(1, volume)), muted: volume <= 0 })
  const toggleMute = () =>
    setSound((s) => (s.muted || s.volume === 0 ? { volume: s.volume || 0.5, muted: false } : { ...s, muted: true }))
  const audible = sound.muted ? 0 : sound.volume

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName ?? ''
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape') go(null)
      else if (e.key === 'ArrowRight' && next) go(next.id)
      else if (e.key === 'ArrowLeft' && prev) go(prev.id)
      else if (e.key === 'i' || e.key === 'I') onToggleInfo()
      else if (e.key === '+' || e.key === '=') zoomIn()
      else if (e.key === '-') zoomOut()
      else if (e.key === 'f' || e.key === 'F') toggleFav()
      else if (e.key === ' ' && isVideo) {
        e.preventDefault()
        togglePlay()
      } else if (e.key === ' ' && asset.liveUrl) {
        e.preventDefault()
        if (liveOn) stopLive()
        else playLive(true)
      } else if ((e.key === 'm' || e.key === 'M') && isVideo) toggleMute()
      else if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && isVideo) {
        e.preventDefault()
        setVolume(audible + (e.key === 'ArrowUp' ? 0.1 : -0.1))
      } else if (e.key === 'Delete') trash()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // ── Edits ──────────────────────────────────────────────────────────────────
  const date = parseLocal(asset.capturedAt)
  const startEdit = (field: Field) => {
    if (editing?.field === field) return
    const value =
      field === 'title' ? asset.title
      : field === 'desc' ? asset.description
      : field === 'date' ? asset.capturedAt.replace('T', ' ')
      : asset.place
    setEditing({ field, value, orig: value, error: false })
  }
  const saveEdit = () => {
    if (!editing) return
    const value = editing.value.trim()
    if (editing.value === editing.orig) return setEditing(null)
    if (editing.field === 'date' && !DATE_RE.test(value)) return setEditing({ ...editing, error: true })
    const patch: AssetPatch =
      editing.field === 'title' ? { title: value }
      : editing.field === 'desc' ? { description: value }
      : editing.field === 'date' ? { capturedAt: (value.length === 16 ? `${value}:00` : value).replace(' ', 'T') }
      : { place: value }
    const label = FIELD_LABEL[editing.field]
    setEditing(null)
    void change(() => api.updateAssets([asset.id], patch), { msg: `${label} saved`, detail: `Written to ${asset.name}` })
  }
  const onEditKey = (e: ReactKeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      setEditing(null)
    } else if (e.key === 'Enter' && !(e.shiftKey && e.currentTarget.tagName === 'TEXTAREA')) {
      e.preventDefault()
      saveEdit()
    }
  }
  const editInput = (extra: React.CSSProperties = {}, placeholder?: string) => ({
    className: 'input editing',
    value: editing?.value ?? '',
    autoFocus: true,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => editing && setEditing({ ...editing, value: e.target.value, error: false }),
    onKeyDown: onEditKey,
    onBlur: saveEdit,
    style: extra,
    ...(placeholder ? { placeholder } : {}),
  })

  // ── Tags and renaming (ORG-02, FOP-01) ─────────────────────────────────────
  const [tagDraft, setTagDraft] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState<string | null>(null)
  const addTag = () => {
    const tag = tagDraft?.trim()
    setTagDraft(null)
    if (tag) void change(() => api.addTag([asset.id], tag), { msg: `Tagged “${tag}”`, detail: `Written to ${asset.name}` })
  }
  const removeTag = (tag: string) =>
    void change(() => api.removeTag([asset.id], tag), { msg: `Removed “${tag}”`, detail: `From ${asset.name}` })
  const renameFile = () => {
    const name = renameDraft?.trim()
    setRenameDraft(null)
    if (name && name !== asset.name.replace(/\.[^.]+$/, '')) {
      void change(() => api.rename(asset.id, name), { msg: `Renamed to ${name}`, detail: asset.pairName ? 'Both files of the pair' : '' })
    }
  }

  const rate = (n: number) => {
    const value = n === asset.rating ? 0 : n
    void change(() => api.updateAssets([asset.id], { rating: value }), {
      msg: value ? `Rated ${value} ${plural(value, 'star')}` : 'Rating cleared',
      detail: asset.name,
    })
  }

  // ── Derived display ────────────────────────────────────────────────────────
  const strip = items.slice(Math.max(0, index - 12), Math.min(items.length, index + 13))
  const place = asset.place
  const hasGps = !!asset.gps && place !== ''
  const mapX = 30 + ((Math.abs(asset.gps?.[1] ?? 0) * 7) % 40)
  const zone = asset.captureOffset === '+10:00' ? 'AEST ' : ''
  // Only what the file states: real files often lack some of these.
  const exposure = isVideo
    ? [duration(asset.durationS), asset.ext]
    : [
        asset.fNumber ? `ƒ/${asset.fNumber}` : '',
        asset.shutter ? `${asset.shutter} s` : '',
        asset.iso ? `ISO ${asset.iso}` : '',
        asset.focalLength ? `${asset.focalLength} mm` : '',
      ].filter(Boolean)
  const lensLine = [asset.lens, asset.focalLength ? `${asset.focalLength} mm` : ''].filter(Boolean).join(' · ')
  const resolution =
    Math.min(asset.width, asset.height) >= 2160 ? '4K UHD'
    : Math.min(asset.width, asset.height) >= 1080 ? 'Full HD'
    : Math.min(asset.width, asset.height) >= 720 ? 'HD'
    : ''
  const pairExt = asset.pairName?.split('.').pop() ?? 'JPG'

  return (
    <div
      className="viewer"
      data-theme="dark"
      data-screen-label="Viewer"
      style={{ gridTemplateColumns: `minmax(0,1fr) ${infoOpen ? 'var(--size-info-panel)' : '0px'}` }}
    >
      <div className="viewer-bar">
        <button type="button" className="pill-btn back" onClick={() => go(null)}>
          <Icon name="arrow-left" size={17} />
          Timeline
        </button>
        <div className="viewer-title">
          <b>{asset.name}</b>
          <span className="num">{`${count(index + 1)} of ${count(items.length)}`}</span>
        </div>
        <div className="grow" />
        <div className="zoom-group">
          <button type="button" title="Zoom out (−)" onClick={zoomOut}><Icon name="zoom-out" size={16} /></button>
          <button type="button" className="zoom-label" title="Fit to screen" onClick={() => zoomTo(1)}>
            {zoom === 1 ? `Fit · ${pct}%` : `${pct}%`}
          </button>
          <button type="button" title="Zoom in (+)" onClick={zoomIn}><Icon name="zoom-in" size={16} /></button>
        </div>
        <div style={{ width: 8 }} />
        <button
          type="button"
          className="icon-btn"
          title="Favourite (F)"
          onClick={toggleFav}
          style={{ color: asset.favourite ? 'var(--color-accent)' : 'var(--color-text)' }}
        >
          <Icon name="heart" size={17} fill={asset.favourite ? 'currentColor' : 'none'} />
        </button>
        {(isRaw || asset.kind === 'heic') && (
          <a className="pill-btn" title="Download the original file" href={asset.originalUrl} download style={{ textDecoration: 'none' }}>
            <Icon name="download" size={15} />
            Download original
            <span className="subtle" style={{ fontSize: 'var(--text-2xs)', fontWeight: 700 }}>{asset.ext}</span>
          </a>
        )}
        <button type="button" className="icon-btn trash" title="Move to Trash (Del)" onClick={trash}>
          <Icon name="trash" size={17} />
        </button>
        <button type="button" className={`icon-btn ${infoOpen ? 'info-on' : 'info-off'}`} title="Info (I)" onClick={onToggleInfo}>
          <Icon name="panel" size={17} />
        </button>
      </div>

      <div className="stage" ref={stageRef} data-stage="1">
        <div
          className={`picture${dragging ? ' dragging' : ''}`}
          {...panHandlers}
          style={{
            width: Math.round(fitW),
            height: Math.round(fitH),
            background: asset.preview,
            transform: `translate(${shown.x}px, ${shown.y}px) scale(${zoom})`,
            cursor: zoom > 1 ? (dragging ? 'grabbing' : 'grab') : 'default',
            touchAction: zoom > 1 ? 'none' : undefined,
          }}
        >
          {asset.videoUrl && (
            <video
              ref={videoRef}
              src={asset.videoUrl}
              preload="metadata"
              playsInline
              onClick={togglePlay}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onTimeUpdate={(e) => setVt(e.currentTarget.currentTime)}
              style={{ width: '100%', height: '100%', display: 'block', objectFit: 'contain', background: 'transparent' }}
            />
          )}
          {asset.liveUrl && (
            <video
              ref={liveRef}
              className={`live-motion${liveOn ? ' on' : ''}`}
              src={asset.liveUrl}
              preload="auto"
              playsInline
              onEnded={() => setLiveOn(false)}
              onError={() => setLiveOn(false)}
            />
          )}
        </div>
        {isVideo && !playing && (
          <button type="button" className="play-big" title="Play (Space)" onClick={togglePlay}>
            <Icon name="play" size={28} fill="currentColor" stroke={0} />
          </button>
        )}
        {isVideo && (
          <div className="video-bar">
            <button type="button" onClick={togglePlay}>
              <Icon name={playing ? 'pause' : 'play'} size={15} fill="currentColor" stroke={0} />
            </button>
            <span style={{ minWidth: 34 }}>{duration(vt)}</span>
            <div className="seek" {...seekHandlers}>
              <div className="track">
                <div style={{ width: `${asset.durationS ? Math.min(100, (vt / asset.durationS) * 100).toFixed(1) : 0}%` }} />
              </div>
            </div>
            <span>{duration(asset.durationS)}</span>
            <div className="volume">
              <button type="button" title={audible ? 'Mute (M)' : 'Unmute (M)'} onClick={toggleMute}>
                <Icon name={audible === 0 ? 'volume-x' : audible < 0.5 ? 'volume-1' : 'volume-2'} size={16} />
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={audible}
                aria-label="Volume"
                title={`Volume ${Math.round(audible * 100)}%`}
                onChange={(e) => setVolume(Number(e.target.value))}
              />
            </div>
          </div>
        )}
        {asset.liveUrl && (
          <button
            type="button"
            className={`live-pill${liveOn ? ' on' : ''}`}
            title="Live Photo · hover to play, click or Space for sound"
            onPointerEnter={(e) => e.pointerType === 'mouse' && playLive(false)}
            onPointerLeave={(e) => e.pointerType === 'mouse' && liveMode.current === 'hover' && stopLive()}
            onClick={() => playLive(true)}
          >
            <Icon name="live" size={14} stroke={2} />
            LIVE
          </button>
        )}
        {isRaw && (
          <div className="raw-note">
            {asset.kind === 'pair' ? `Showing paired ${pairExt} · ${asset.ext} original kept alongside` : `Showing embedded preview from ${asset.ext}`}
          </div>
        )}
        {prev && (
          <button type="button" className="stage-nav prev" title="Previous (←)" onClick={() => go(prev.id)}>
            <Icon name="chevron-left" size={22} />
          </button>
        )}
        {next && (
          <button type="button" className="stage-nav next" title="Next (→)" onClick={() => go(next.id)}>
            <Icon name="chevron-right" size={22} />
          </button>
        )}
      </div>

      <div className="filmstrip">
        {strip.map((x) => (
          <div
            key={x.id}
            className={x.id === asset.id ? 'current' : ''}
            onClick={() => go(x.id)}
            style={{ width: Math.round(Math.max(38, Math.min(100, (56 * x.width) / x.height))), background: x.thumb }}
          />
        ))}
      </div>

      {infoOpen && (
        <aside className="info">
          <div className="info-head">
            <span className="caps">Info</span>
            <button type="button" className="icon-btn xs" title="Hide info" onClick={onToggleInfo}>
              <Icon name="x" size={15} />
            </button>
          </div>

          <div className="info-section tight">
            {editing?.field === 'title' ? (
              <input {...editInput({ fontSize: 'var(--text-lg)', fontWeight: 700 }, 'Add a title')} />
            ) : (
              <div className="editable" style={{ alignItems: 'center' }} onClick={() => startEdit('title')}>
                <span className="title-text" style={{ color: asset.title ? 'var(--color-text)' : 'var(--color-text-subtle)' }}>
                  {asset.title || 'Add a title'}
                </span>
                <Icon name="pencil" size={13} className="pencil" style={{ marginTop: 0 }} />
              </div>
            )}
            {editing?.field === 'desc' ? (
              <textarea {...editInput({ minHeight: 84, fontSize: 'var(--text-sm)', borderRadius: 12 }, 'Add a description')} />
            ) : (
              <div className="editable" onClick={() => startEdit('desc')}>
                <span className="desc-text" style={{ color: asset.description ? 'var(--color-text-muted)' : 'var(--color-text-subtle)' }}>
                  {asset.description || 'Add a description'}
                </span>
                <Icon name="pencil" size={13} className="pencil" />
              </div>
            )}
            {editing && <span className="edit-hint">Enter to save · Esc to cancel</span>}
            <div className="stars">
              {[1, 2, 3, 4, 5].map((n) => (
                <button key={n} type="button" className={n <= asset.rating ? 'on' : ''} onClick={() => rate(n)}>
                  <Icon name="star" size={16} fill={n <= asset.rating ? 'currentColor' : 'none'} />
                </button>
              ))}
            </div>
          </div>

          <div className="info-section facts">
            <div className="editable row" onClick={() => startEdit('date')}>
              <Icon name="calendar" size={17} style={{ color: 'var(--color-text-subtle)', marginTop: 2 }} />
              {editing?.field === 'date' ? (
                <div className="grow" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <input {...editInput({ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', minHeight: 32 })} />
                  <span className="edit-hint" style={editing.error ? { color: 'var(--color-critical)' } : undefined}>
                    {editing.error ? 'Use YYYY-MM-DD HH:MM:SS' : 'YYYY-MM-DD HH:MM:SS · local time'}
                  </span>
                </div>
              ) : (
                <>
                  <div className="grow num" style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.35 }}>
                    {asset.dateApprox ? (
                      <>
                        <span style={{ fontWeight: 600 }}>{asset.dateApprox.label}</span>
                        <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                          No date in the file · placed by its folder
                        </span>
                      </>
                    ) : (
                      <>
                        <span style={{ fontWeight: 600 }}>{`${DAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}`}</span>
                        <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                          {`${timeLabel(date)} · ${zone}(UTC${asset.captureOffset})${asset.dateEdited ? ' · edited' : ''}`}
                        </span>
                      </>
                    )}
                  </div>
                  <Icon name="pencil" size={13} className="pencil" />
                </>
              )}
            </div>
            <div className="fact">
              <Icon name="camera" size={17} />
              <div className="lines">
                <b>{asset.camera || 'Camera not recorded'}</b>
                {lensLine && <small>{lensLine}</small>}
              </div>
            </div>
            {exposure.length > 0 && (
              <div className="fact">
                <Icon name="aperture" size={17} />
                <div className="exposure num">
                  {exposure.map((v) => <span key={v}>{v}</span>)}
                </div>
              </div>
            )}
            <div className="fact">
              <Icon name="image" size={17} />
              <div className="lines num">
                <b>{`${asset.width} × ${asset.height}`}</b>
                <small>{isVideo ? resolution : `${((asset.width * asset.height) / 1e6).toFixed(1)} megapixels`}</small>
              </div>
            </div>
            <div className="fact">
              <Icon name="file" size={17} />
              <div className="lines num" style={{ gap: 2 }}>
                <b>{`${bytes(asset.size)} · ${asset.ext}`}</b>
                {asset.pairName && <small>{`Paired with ${asset.pairName} · ${bytes(asset.pairSize)}`}</small>}
                {asset.liveUrl && <small>Live Photo · its motion clip is kept with it</small>}
                <span className="path">{asset.folder + asset.name}</span>
                {asset.missing && (
                  <small style={{ color: 'var(--color-critical)' }}>Not found by the last scan</small>
                )}
                {renameDraft === null ? (
                  <button type="button" className="explorer" onClick={() => setRenameDraft(asset.name.replace(/\.[^.]+$/, ''))}>
                    <Icon name="pencil" size={13} />
                    Rename file{asset.pairName ? 's' : ''}
                  </button>
                ) : (
                  <input
                    className="input mono"
                    autoFocus
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      e.stopPropagation()
                      if (e.key === 'Enter') renameFile()
                      if (e.key === 'Escape') setRenameDraft(null)
                    }}
                    onBlur={() => setRenameDraft(null)}
                    style={{ minHeight: 30, fontSize: 'var(--text-xs)', marginTop: 4 }}
                  />
                )}
              </div>
            </div>
          </div>

          <div className="info-section">
            <span className="caps">Location</span>
            <div
              className="mini-map"
              style={{
                backgroundImage: `radial-gradient(ellipse 60% 70% at ${mapX}% 60%, rgb(174 191 146 / 0.16), transparent 70%), linear-gradient(var(--color-divider) 1px, transparent 1px), linear-gradient(90deg, var(--color-divider) 1px, transparent 1px)`,
              }}
            >
              {hasGps ? (
                <div className="pin">
                  <Icon name="pin" size={28} fill="var(--color-surface-raised)" stroke={2.5} />
                </div>
              ) : (
                <div className="none">No location in this file</div>
              )}
              <span className="credit">© OpenStreetMap</span>
            </div>
            <div className="editable row" onClick={() => startEdit('place')}>
              <Icon name="pin" size={17} style={{ color: 'var(--color-text-subtle)', marginTop: 2 }} />
              {editing?.field === 'place' ? (
                <input {...editInput({ flex: 1, fontSize: 'var(--text-sm)', minHeight: 32 }, 'Search for a place')} />
              ) : (
                <>
                  <div className="grow num" style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.35 }}>
                    <span style={{ fontWeight: 600, color: place ? 'var(--color-text)' : 'var(--color-text-subtle)' }}>{place || 'Add a location'}</span>
                    <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                      {asset.gps ? `${asset.gps[0].toFixed(4)}°, ${asset.gps[1].toFixed(4)}°` : 'No GPS in file'}
                    </span>
                  </div>
                  <Icon name="pencil" size={13} className="pencil" />
                </>
              )}
            </div>
          </div>

          <div className="info-section">
            <span className="caps">Tags</span>
            <div className="chips">
              {asset.tags.map((t) => (
                <span key={t} className="tagc removable">
                  {t}
                  <button type="button" title={`Remove “${t}”`} onClick={() => removeTag(t)}>
                    <Icon name="x" size={10} />
                  </button>
                </span>
              ))}
              {tagDraft === null ? (
                <button type="button" className="add" onClick={() => setTagDraft('')}>
                  <Icon name="plus" size={12} />
                  Tag
                </button>
              ) : (
                <input
                  className="input tag-input"
                  autoFocus
                  placeholder="Tag, then Enter"
                  value={tagDraft}
                  onChange={(e) => setTagDraft(e.target.value)}
                  onKeyDown={(e) => {
                    e.stopPropagation()
                    if (e.key === 'Enter') addTag()
                    if (e.key === 'Escape') setTagDraft(null)
                  }}
                  onBlur={() => setTagDraft(null)}
                />
              )}
            </div>
          </div>

          <div className="info-section">
            <span className="caps">Albums</span>
            <div className="chips">
              {asset.albums.map((a) => (
                <span key={a} className="albumc">
                  <Icon name="album" size={12} />
                  {a}
                </span>
              ))}
              {!asset.albums.length && <span className="subtle" style={{ fontSize: 'var(--text-sm)' }}>Not in any album</span>}
            </div>
          </div>

          <div className="info-foot">
            <Icon name="lock" size={13} />
            <span style={{ textWrap: 'pretty' }}>
              Edits are written into the file’s metadata, or an .xmp file beside a RAW or video. Each one can be undone from Activity.
            </span>
          </div>
        </aside>
      )}
    </div>
  )
}
