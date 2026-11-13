/**
 * The HTTP client and every endpoint the screens call.
 *
 * Requests are always relative: in production nginx and Gunicorn serve the
 * bundle and /api from one origin, and in development Vite proxies /api and
 * /media to Django.
 *
 * Live by default. Endpoints whose phase has not landed answer honestly - an
 * empty list, or a refusal naming the phase - rather than with sample data
 * mixed into the real library. Demo mode (`?mock=<state>`, see ./mock/server)
 * routes everything to the design's sample library instead.
 */

import { mock, type AssetPatch } from './mock/server'
import type {
  AlbumSummary,
  AppSettings,
  Asset,
  AssetPage,
  MapPoints,
  DuplicateQueue,
  FolderBrowse,
  FolderListing,
  LibraryInfo,
  MediaKind,
  Operation,
  ScanDetails,
  ScanStatus,
  SettingsPatch,
  TrashListing,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  /** The first message per field, for errors shown beside their input. */
  readonly fields: Readonly<Record<string, string>>

  constructor(status: number, message: string, code: string | null = null, fields: Record<string, string> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.fields = fields
  }

  get isUnauthenticated(): boolean {
    return this.status === 401 || this.status === 403
  }
}

/** True when the app is showing the design's sample library. */
export const demo = mock.scenario !== null

const UNREACHABLE =
  'The Gallery did not answer. Check that the Django development server is running on the port named in .env.'

/**
 * Not Django's default name: financial-hub also runs on localhost in
 * development, and cookies are not separated by port. Read fresh before every
 * request, because signing in rotates the token.
 */
function csrfToken(): string {
  const value = document.cookie.match(/(?:^|;\s*)gallery_csrftoken=([^;]*)/)?.[1]
  return value ? decodeURIComponent(value) : ''
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method)) headers.set('X-CSRFToken', csrfToken())

  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...init, method, headers, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, UNREACHABLE)
  }
  if (response.status === 204) return undefined as T

  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    if (!response.ok) throw new ApiError(response.status, UNREACHABLE)
  }
  if (!response.ok) {
    const b = body as {
      error?: { message?: string; code?: string; field_errors?: Record<string, { message?: string }[]> }
      detail?: string
    } | null
    const fields: Record<string, string> = {}
    for (const [field, items] of Object.entries(b?.error?.field_errors ?? {})) {
      const first = items[0]?.message
      if (first) fields[field] = first
    }
    throw new ApiError(response.status, b?.error?.message ?? b?.detail ?? UNREACHABLE, b?.error?.code ?? null, fields)
  }
  return body as T
}

const http = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
}

export interface SessionState {
  readonly authenticated: boolean
  readonly username: string | null
}

/** What every change answers with. `pending` means the worker is still on it. */
export interface OperationRef {
  readonly operationId: string
  readonly state?: Operation['state']
  readonly error?: string | null
  readonly summary?: string
}

// ── The asset as the server sends it ────────────────────────────────────────

interface AssetDto extends Omit<Asset, 'thumb' | 'preview'> {
  readonly kind: MediaKind
  readonly thumbUrl: string
  readonly previewUrl: string
  readonly thumbReady: boolean
}

const NO_ART = 'var(--color-surface-sunken)'

function toAsset({ thumbUrl, previewUrl, thumbReady, ...rest }: AssetDto): Asset {
  // Not ready yet (the worker is still on it): a plain tile, not a broken image.
  const thumb = thumbReady ? `url("${thumbUrl}") center / cover no-repeat, ${NO_ART}` : NO_ART
  const preview = thumbReady ? `url("${previewUrl}") center / contain no-repeat, ${thumb}` : NO_ART
  return { ...rest, thumb, preview }
}

const live = {
  async assetsPage(query: string, folder: string, album: string, area: string, cursor: string | null): Promise<AssetPage> {
    const params = new URLSearchParams({ limit: '1000' })
    if (query.trim()) params.set('q', query.trim())
    if (folder) params.set('folder', folder)
    if (album) params.set('album', album)
    if (area) params.set('area', area)
    if (cursor) params.set('cursor', cursor)
    const page = await http.get<{ results: AssetDto[]; next: string | null }>(`/assets?${params}`)
    return { results: page.results.map(toAsset), next: page.next }
  },
  async trashListing(): Promise<TrashListing> {
    const body = await http.get<{ items: (AssetDto & { trashedAt: string | null; purgeAt: string | null })[]; count: number; retentionDays: number }>('/trash')
    return { ...body, items: body.items.map((i) => ({ ...toAsset(i), trashedAt: i.trashedAt, purgeAt: i.purgeAt })) }
  },
}

const DEMO_SETTINGS: AppSettings = {
  libraryRoot: 'D:\\Photos',
  libraryPath: 'D:\\Photos',
  mustChoose: false,
  librarySubfolder: '',
  exclusions: ['Exports'],
  scanIntervalMinutes: 60,
  trashRetentionDays: 30,
  writeBackMode: 'embedded',
  phashThreshold: 4,
  autoMergeExact: false,
}

/** A change whose phase has not landed. Refused, so nothing looks saved that was not. */
function notYet(phase: string, what: string): Promise<never> {
  // useChange's toast already says "Nothing was changed"; this says why.
  return Promise.reject(new ApiError(501, `${what} arrives in Phase ${phase}.`, 'not_built'))
}

export const api = {
  // ── Auth (always live) ─────────────────────────────────────────────────────
  /** Never an error; also sets the CSRF cookie, so it is the first call made. */
  session: () => http.get<SessionState>('/auth/session'),
  login: (password: string) => http.post<SessionState>('/auth/login', { password }),
  logout: () => http.post<void>('/auth/logout'),

  // ── Phase 1a ───────────────────────────────────────────────────────────────
  library: (): Promise<LibraryInfo> => (demo ? mock.library() : http.get<LibraryInfo>('/library')),
  /** `area` is a box on the map, "south,west,north,east" (MAP-01). */
  assetsPage: (query: string, folder: string, album: string, area: string, cursor: string | null): Promise<AssetPage> =>
    demo ? mock.assets(query).then((results) => ({ results, next: null })) : live.assetsPage(query, folder, album, area, cursor),
  /** One level of the folder tree (BRW-03). The design's sample library has no folder view. */
  folderListing: (path: string): Promise<FolderListing> =>
    demo
      ? Promise.resolve({ path, base: '', title: '', folders: [], looseCount: 0 })
      : http.get<FolderListing>(`/folders?${new URLSearchParams(path ? { path } : {})}`),
  scanStatus: (): Promise<ScanStatus> => (demo ? mock.scanStatus() : http.get<ScanStatus>('/scan/status')),
  startScan: (): Promise<ScanStatus> => (demo ? mock.startScan() : http.post<ScanStatus>('/scan')),
  scanDetails: async (): Promise<ScanDetails> => {
    if (!demo) return http.get<ScanDetails>('/scan/details')
    const status = await mock.scanStatus()
    return {
      status,
      worker: { online: true, processes: 4, lastSeenAt: new Date().toISOString() },
      queue: {},
      failed: [],
      failedTotal: status.failedFiles,
      recent: [],
    }
  },
  retryFailed: (): Promise<{ retried: number; status: ScanStatus }> =>
    demo ? notYet('1a', 'Retrying in demo mode') : http.post('/scan/retry'),

  settings: (): Promise<AppSettings> => (demo ? Promise.resolve(DEMO_SETTINGS) : http.get<AppSettings>('/settings')),
  browseFolders: (path: string): Promise<FolderBrowse> =>
    demo
      ? Promise.resolve({ path, display: 'D:\\Photos', parent: null, folders: [], canChoose: true })
      : http.get<FolderBrowse>(`/settings/browse?${new URLSearchParams(path ? { path } : {})}`),
  saveSettings: (patch: SettingsPatch): Promise<{ settings: AppSettings; scanStarted: boolean }> =>
    demo
      ? Promise.resolve({ settings: { ...DEMO_SETTINGS, ...patch }, scanStarted: false })
      : request('/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  // ── Phase 1b: organise ─────────────────────────────────────────────────────
  // Every change answers with an operation (OperationRef). Those the worker
  // carries out come back `pending`; useChange polls them to the end.
  trashCount: (): Promise<number> =>
    demo ? mock.trashCount() : http.get<{ count: number }>('/trash/count').then((r) => r.count),
  trashListing: (): Promise<TrashListing> =>
    demo ? Promise.resolve({ items: [], count: 0, retentionDays: 30 }) : live.trashListing(),
  albums: (): Promise<AlbumSummary[]> => (demo ? mock.albums() : http.get<AlbumSummary[]>('/albums')),
  /** Move destinations: folders inside the library folder that hold something. */
  folders: (): Promise<string[]> => (demo ? mock.folders() : http.get<string[]>('/folders/targets')),
  tagSuggestions: (): Promise<string[]> => (demo ? mock.tagSuggestions() : http.get<string[]>('/tags')),
  operations: (): Promise<Operation[]> => (demo ? Promise.resolve([]) : http.get<Operation[]>('/operations?limit=40')),

  updateAssets: (ids: readonly string[], patch: AssetPatch): Promise<OperationRef> =>
    demo ? mock.updateAssets(ids, patch) : http.post<OperationRef>('/assets/bulk', { ids, ...patch }),
  addTag: (ids: readonly string[], tag: string): Promise<OperationRef> =>
    demo ? mock.addTag(ids, tag) : http.post<OperationRef>('/assets/bulk', { ids, addTags: [tag] }),
  removeTag: (ids: readonly string[], tag: string): Promise<OperationRef> =>
    demo ? notYet('1b', 'Removing tags in demo mode') : http.post<OperationRef>('/assets/bulk', { ids, removeTags: [tag] }),
  addToAlbum: (ids: readonly string[], album: string): Promise<OperationRef> =>
    demo ? mock.addToAlbum(ids, album) : http.post<OperationRef>('/albums/add', { ids, name: album }),
  removeFromAlbum: (albumId: string, ids: readonly string[]): Promise<OperationRef> =>
    http.post<OperationRef>(`/albums/${albumId}/remove`, { ids }),
  createAlbum: (name: string): Promise<OperationRef> => http.post<OperationRef>('/albums', { name }),
  renameAlbum: (albumId: string, name: string): Promise<OperationRef> =>
    request<OperationRef>(`/albums/${albumId}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  deleteAlbum: (albumId: string): Promise<OperationRef> => request<OperationRef>(`/albums/${albumId}`, { method: 'DELETE' }),
  move: (ids: readonly string[], folder: string): Promise<OperationRef> =>
    demo ? mock.move(ids, folder) : http.post<OperationRef>('/operations', { kind: 'move', ids, folder }),
  rename: (id: string, name: string): Promise<OperationRef> =>
    http.post<OperationRef>('/operations', { kind: 'rename', ids: [id], name }),
  trash: (ids: readonly string[]): Promise<OperationRef> =>
    demo ? mock.trash(ids) : http.post<OperationRef>('/operations', { kind: 'trash', ids }),
  restore: (ids: readonly string[]): Promise<OperationRef> =>
    http.post<OperationRef>('/operations', { kind: 'restore', ids }),
  purge: (ids: readonly string[]): Promise<OperationRef> => http.post<OperationRef>('/operations', { kind: 'purge', ids }),
  emptyTrash: (): Promise<OperationRef> => http.post<OperationRef>('/trash/empty'),

  // ── Phase 1d: the map ──────────────────────────────────────────────────────
  mapPoints: (): Promise<MapPoints> =>
    demo ? Promise.resolve({ points: [], count: 0, placesLoaded: false }) : http.get<MapPoints>('/map/points'),

  // ── Phase 1c: duplicates ───────────────────────────────────────────────────
  duplicates: (): Promise<DuplicateQueue> => (demo ? mock.duplicates() : Promise.resolve({ total: 0, groups: [] })),
  merge: (groupId: string, retry: boolean): Promise<OperationRef> =>
    demo ? mock.merge(groupId, retry) : notYet('1c', 'Merging'),
  dismissDuplicate: (groupId: string): Promise<OperationRef> =>
    demo ? mock.dismissDuplicate(groupId) : notYet('1c', 'Duplicate review'),
  trashSimilar: (groupId: string): Promise<OperationRef> =>
    demo ? mock.trashSimilar(groupId) : notYet('1c', 'Duplicate review'),

  operation: (id: string): Promise<Operation> => (demo ? mock.operation(id) : http.get<Operation>(`/operations/${id}`)),
  /** Starts the undo; an undo of a file change comes back `undoing` - poll operation(). */
  undo: (id: string): Promise<Operation | void> =>
    demo ? mock.undo(id) : http.post<Operation>(`/operations/${id}/undo`),
}

export type { AssetPatch }
