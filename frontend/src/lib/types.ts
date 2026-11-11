/**
 * Shapes the screens render. They follow the HLD's data model (Asset, MediaFile,
 * DuplicateGroup, Operation) as the API will serialise them, flattened to what
 * the UI needs. Dates travel as local wall-clock strings, "YYYY-MM-DDTHH:mm:ss",
 * with the offset alongside, because a photo's capture time is a local fact.
 */

export type MediaKind = 'jpg' | 'heic' | 'raw' | 'pair' | 'video'

export interface Asset {
  readonly id: string
  readonly capturedAt: string
  readonly captureOffset: string
  readonly kind: MediaKind
  readonly ext: string
  readonly name: string
  readonly pairName: string | null
  readonly width: number
  readonly height: number
  readonly durationS: number
  readonly size: number
  readonly pairSize: number
  readonly camera: string
  readonly lens: string
  readonly focalLength: number
  readonly fNumber: number
  readonly shutter: string
  readonly iso: number
  readonly place: string
  readonly gps: readonly [number, number] | null
  readonly folder: string
  readonly title: string
  readonly description: string
  readonly dateEdited: boolean
  readonly favourite: boolean
  readonly rating: number
  readonly tags: readonly string[]
  readonly albums: readonly string[]
  /** A CSS background: the grid thumbnail, or painted placeholder art in demo mode. */
  readonly thumb: string
  /** A CSS background for the viewer: the 2,048 px preview over the thumbnail. */
  readonly preview: string
  /** The file itself - the RAW of a pair. Offered as a download for HEIC and RAW. */
  readonly originalUrl: string
  /** Streams the video, for video assets. */
  readonly videoUrl: string | null
  /** A Live Photo's motion clip, played over the still in the viewer. Null for anything else. */
  readonly liveUrl: string | null
  /** Not found by the last scan; it drops out after the next one if still gone. */
  readonly missing: boolean
  /**
   * Set when the file states no date and `capturedAt` comes from its folder
   * (`180221 Penang`): a trip's first day, or a whole month or year. Shown as
   * the label, never as an exact time.
   */
  readonly dateApprox: DateApprox | null
}

export interface DateApprox {
  readonly precision: 'day' | 'month' | 'year'
  /** 'On or after 21 Feb 2018', 'Sometime in 2017'. */
  readonly label: string
}

/** One folder in the library, shown as an album card (BRW-03). */
export interface FolderCard {
  readonly path: string
  readonly name: string
  /** The name without its date prefix: 'Penang'. */
  readonly title: string
  /** ISO date from the name, or null for a folder without one. */
  readonly date: string | null
  readonly precision: DateApprox['precision'] | null
  /** '21–25 Feb 2018', '2017', 'from 21 Feb 2018'. */
  readonly span: string
  readonly count: number
  readonly subfolders: number
  readonly coverUrl: string | null
}

export interface FolderListing {
  readonly path: string
  /** The library folder: the top of the Folders view. */
  readonly base: string
  readonly title: string
  readonly folders: readonly FolderCard[]
  /** Items directly in this folder, not in a subfolder. */
  readonly looseCount: number
}

export interface AssetPage {
  readonly results: readonly Asset[]
  readonly next: string | null
}

export interface LibraryInfo {
  readonly path: string | null
  readonly itemCount: number
  readonly totalBytes: number
}

export type ScanState = 'idle' | 'scanning' | 'errors' | 'none'

export interface ScanStatus {
  readonly state: ScanState
  readonly firstScan: boolean
  readonly done: number
  readonly total: number
  /** ISO 8601, when the last scan finished; null before the first. */
  readonly lastScanAt: string | null
  readonly failedFiles: number
  /** Why the last scan failed outright (e.g. the folder vanished). */
  readonly message: string | null
  /** Jobs still queued or running - thumbnails carry on after a scan. */
  readonly pending: number
  /** False when no worker process has been heard from in the last minute. */
  readonly workerOnline: boolean
}

export interface WorkerHealth {
  readonly online: boolean
  readonly processes: number
  readonly lastSeenAt: string | null
}

export interface FailedJob {
  readonly id: number
  readonly kind: string
  readonly path: string
  readonly error: string
  readonly attempts: number
}

export interface ScanRunSummary {
  readonly id: number
  readonly trigger: 'manual' | 'schedule'
  readonly state: 'queued' | 'walking' | 'indexing' | 'done' | 'failed'
  readonly startedAt: string
  readonly finishedAt: string | null
  readonly seen: number
  readonly new: number
  readonly changed: number
  readonly moved: number
  readonly missing: number
  readonly error: string | null
}

/** Behind the scan pill (LIB-06): it doubles as the health view. */
export interface ScanDetails {
  readonly status: ScanStatus
  readonly worker: WorkerHealth
  /** Queued or running jobs by kind. */
  readonly queue: Readonly<Record<string, number>>
  readonly failed: readonly FailedJob[]
  readonly failedTotal: number
  readonly recent: readonly ScanRunSummary[]
}

export type WriteBackMode = 'embedded' | 'sidecar'

/** The settings screen (LIB-01). */
export interface AppSettings {
  /** Fixed by .env; shown, not edited. Null when there is no library. */
  readonly libraryRoot: string | null
  readonly libraryPath: string | null
  /** True where whole drives are mounted (production): a folder must be chosen. */
  readonly mustChoose: boolean
  readonly librarySubfolder: string
  readonly exclusions: readonly string[]
  readonly scanIntervalMinutes: number
  readonly trashRetentionDays: number
  readonly writeBackMode: WriteBackMode
  readonly phashThreshold: number
  readonly autoMergeExact: boolean
}

export type SettingsPatch = Partial<Omit<AppSettings, 'libraryRoot' | 'libraryPath' | 'mustChoose'>>

/** One level of the folder browser for choosing the library folder. */
export interface FolderBrowse {
  readonly path: string
  /** 'D:\\Media\\Photos', or 'This PC' at the top. */
  readonly display: string
  /** null at the top. */
  readonly parent: string | null
  readonly folders: readonly { readonly name: string; readonly path: string }[]
  readonly canChoose: boolean
}

export interface AlbumSummary {
  /** Absent in demo mode's sample albums. */
  readonly id?: string
  readonly name: string
  readonly count: number
  readonly coverUrl?: string | null
}

/** An item in the trash: where it came from, and when it goes for good. */
export interface TrashItem extends Asset {
  readonly trashedAt: string | null
  readonly purgeAt: string | null
}

export interface TrashListing {
  readonly items: readonly TrashItem[]
  readonly count: number
  readonly retentionDays: number
}

export type DuplicateKind = 'visual' | 'exact' | 'similar'

export interface DuplicateCopy {
  readonly name: string
  readonly path: string
  readonly width: number
  readonly height: number
  readonly size: number
  readonly format: string
  readonly modified: string
  readonly thumb: string
}

export type MergeField = 'date' | 'gps' | 'camera' | 'lens' | 'keywords' | 'rating' | 'title' | 'description'
export type MetaValue = string | number | readonly string[] | null

export interface DuplicateGroup {
  readonly id: string
  readonly kind: DuplicateKind
  readonly title: string
  /** Why the proposed keeper was proposed (BRD, Keeper selection). */
  readonly keeperReason: string
  readonly proposedKeeper: number
  readonly copies: readonly DuplicateCopy[]
  /** One value per copy for each field; empty for a similar group. */
  readonly meta: Partial<Record<MergeField, readonly MetaValue[]>>
}

export interface DuplicateQueue {
  readonly total: number
  readonly groups: readonly DuplicateGroup[]
}

export type OperationState = 'pending' | 'applied' | 'undoing' | 'undone' | 'failed'

/** One entry in the operation log (FOP-03). */
export interface Operation {
  readonly id: string
  readonly state: OperationState
  /** Steps done so far (a merge shows three of them). */
  readonly step: number
  readonly error: string | null
  readonly kind?: string
  /** 'Moved 12 items to Trash'. */
  readonly summary?: string
  readonly undoable?: boolean
  readonly createdAt?: string
  readonly finishedAt?: string | null
}
