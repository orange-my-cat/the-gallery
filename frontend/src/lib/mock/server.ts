/**
 * Demo mode: an in-memory stand-in for the whole API, with the design's sample
 * library. It behaves like the real endpoints where the screens can tell:
 * every change returns an operation id that can be undone, a merge runs as a
 * pending operation the client polls, and scans progress over time. Duplicate
 * review has no demo: it runs on the real library (Phase 1c).
 *
 * Off unless asked for. Open the app with ?mock=<state> to review a state from
 * the design; it sticks for the browser tab until ?mock=off.
 *   normal · firstScan · empty · scanning · errors
 */

import { count, plural } from '../format'
import type { Asset, LibraryInfo, Operation, ScanStatus } from '../types'
import { buildLibrary, LIBRARY_PATH, SAMPLE_ALBUMS, SAMPLE_FOLDERS, SAMPLE_TAGS } from './library'

export type Scenario = 'normal' | 'firstScan' | 'empty' | 'scanning' | 'errors'
const SCENARIOS: readonly Scenario[] = ['normal', 'firstScan', 'empty', 'scanning', 'errors']

function readScenario(): Scenario | null {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('mock')
    if (fromUrl === 'off') sessionStorage.removeItem('gallery.mock')
    else if (fromUrl && (SCENARIOS as readonly string[]).includes(fromUrl)) sessionStorage.setItem('gallery.mock', fromUrl)
    const stored = sessionStorage.getItem('gallery.mock')
    return stored && (SCENARIOS as readonly string[]).includes(stored) ? (stored as Scenario) : null
  } catch {
    return null
  }
}

const scenario = readScenario()
const library = buildLibrary()
const byId = new Map(library.map((a) => [a.id, a]))
const trashed = new Set<string>()
const PREVIOUSLY_TRASHED = 312

/** Scan progress is a function of elapsed time, as it would be on the server. */
let scan = {
  startedAt: scenario === 'firstScan' || scenario === 'scanning' ? Date.now() : 0,
  from: scenario === 'firstScan' ? 80 : scenario === 'scanning' ? 740 : library.length,
  perSecond: scenario === 'firstScan' ? 36 : scenario === 'scanning' ? 8 : 160,
  firstScan: scenario === 'firstScan',
  lastScanAt: scenario === 'errors' ? todayAt(9, 12) : new Date(Date.now() - 2 * 60000).toISOString(),
  failedFiles: scenario === 'errors' ? 12 : 0,
}

function todayAt(h: number, m: number): string {
  const d = new Date()
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}

function scanProgress(): ScanStatus {
  const total = scenario === 'empty' ? 0 : library.length
  const base = { lastScanAt: scan.lastScanAt, failedFiles: 0, message: null, pending: 0, workerOnline: true }
  if (scenario === 'empty') return { ...base, state: 'none', firstScan: false, done: 0, total, lastScanAt: null }
  if (scan.startedAt) {
    const done = Math.min(total, Math.floor(scan.from + ((Date.now() - scan.startedAt) / 1000) * scan.perSecond))
    if (done < total) return { ...base, state: 'scanning', firstScan: scan.firstScan, done, total }
    scan = { ...scan, startedAt: 0, from: total, firstScan: false, lastScanAt: new Date().toISOString(), failedFiles: 0 }
  }
  return {
    ...base, state: scan.failedFiles ? 'errors' : 'idle', firstScan: false, done: total, total,
    lastScanAt: scan.lastScanAt, failedFiles: scan.failedFiles,
  }
}

// ── Operations: the undo log (FOP-03) ───────────────────────────────────────

interface LoggedOp {
  undo: () => void
  undone: boolean
}
const operations = new Map<string, LoggedOp>()
let opSeq = 1

function record(undo: () => void): { operationId: string } {
  const id = `op${opSeq++}`
  operations.set(id, { undo, undone: false })
  return { operationId: id }
}

/** Every demo change applies at once. */
function operationState(id: string): Operation {
  const op = operations.get(id)
  if (!op) return { id, state: 'failed', step: 0, error: 'Unknown operation' }
  return { id, state: op.undone ? 'undone' : 'applied', step: 0, error: null }
}

// ── Edits ───────────────────────────────────────────────────────────────────

function patchAssets(ids: readonly string[], change: (a: Asset) => Partial<Asset>): { operationId: string } {
  const before = ids.map((id) => byId.get(id)).filter((a): a is Asset => !!a)
  before.forEach((a) => byId.set(a.id, { ...a, ...change(a) }))
  return record(() => before.forEach((a) => byId.set(a.id, a)))
}

const delay = <T,>(value: T, ms = 60) => new Promise<T>((resolve) => setTimeout(() => resolve(value), ms))

export type AssetPatch = Partial<Pick<Asset, 'title' | 'description' | 'place' | 'capturedAt' | 'favourite' | 'rating'>>

export const mock = {
  scenario,

  library(): Promise<LibraryInfo> {
    if (scenario === 'empty') return delay({ path: null, itemCount: 0, totalBytes: 0 })
    const totalBytes = library.reduce((sum, a) => sum + a.size + a.pairSize, 0)
    return delay({ path: LIBRARY_PATH, itemCount: library.length, totalBytes })
  },

  /** Every visible asset, newest first. The real endpoint pages by cursor (capture_at, id). */
  assets(query: string): Promise<Asset[]> {
    if (scenario === 'empty') return delay([])
    const status = scanProgress()
    let list = library.map((a) => byId.get(a.id) as Asset)
    if (scan.firstScan && status.state === 'scanning') list = list.slice(0, status.done)
    list = list.filter((a) => !trashed.has(a.id))
    const q = query.trim().toLowerCase()
    if (q) list = list.filter((a) => `${a.place} ${a.name} ${a.camera} ${a.tags.join(' ')}`.toLowerCase().includes(q))
    return delay(list)
  },

  trashCount: () => delay(PREVIOUSLY_TRASHED + trashed.size),
  scanStatus: () => delay(scanProgress()),
  startScan(): Promise<ScanStatus> {
    if (scanProgress().state !== 'scanning') {
      scan = { ...scan, startedAt: Date.now(), from: 0, perSecond: 160, firstScan: false, failedFiles: 0 }
    }
    return delay(scanProgress())
  },

  albums: () => delay(SAMPLE_ALBUMS.map(([name, n]) => ({ name, count: n }))),
  folders: () => delay([...SAMPLE_FOLDERS]),
  tagSuggestions: () => delay([...SAMPLE_TAGS]),

  updateAssets: (ids: readonly string[], patch: AssetPatch) =>
    delay(patchAssets(ids, () => ({ ...patch, ...(patch.capturedAt ? { dateEdited: true } : {}) }))),
  addTag: (ids: readonly string[], tag: string) =>
    delay(patchAssets(ids, (a) => ({ tags: a.tags.includes(tag) ? a.tags : [...a.tags, tag] }))),
  addToAlbum: (ids: readonly string[], album: string) =>
    delay(patchAssets(ids, (a) => ({ albums: a.albums.includes(album) ? a.albums : [...a.albums, album] }))),
  move: (ids: readonly string[], folder: string) => delay(patchAssets(ids, () => ({ folder: `${folder}\\` }))),
  trash(ids: readonly string[]) {
    ids.forEach((id) => trashed.add(id))
    return delay(record(() => ids.forEach((id) => trashed.delete(id))))
  },

  operation: (id: string) => delay(operationState(id), 30),
  undo(id: string) {
    const op = operations.get(id)
    if (op && !op.undone) {
      op.undo()
      op.undone = true
    }
    return delay(undefined)
  },
}

export const describeCount = (n: number, noun: string) => `${count(n)} ${plural(n, noun)}`
