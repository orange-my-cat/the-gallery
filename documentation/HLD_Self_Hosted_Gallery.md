# Self-Hosted Gallery — High-Level Design

Nov 8, 2026 · @Ivan

## Architecture at a glance

&#91;embedded content: Containers, storage and what talks to what\]

The browser only ever talks to Nginx. Django checks every request, but photo and video bytes come straight from Nginx's read-only mounts. Only the worker ever changes the library.

## Technology choices

The financial-hub stack carries over whole. The only additions are media libraries and a worker process. The worker runs from the same image and adds no new service: no Redis, and no separate queue.

| Concern | Choice | Origin |
| --- | --- | --- |
| API and auth | Django 5.2 + DRF 3.18, session-cookie login, CSRF | Carried over |
| Database | PostgreSQL 18 via psycopg 3 | Carried over |
| Web server | Gunicorn, with WhiteNoise serving the built frontend | Carried over |
| Frontend | React 19, strict TypeScript, Vite, React Router 7, TanStack Query, plain CSS with `tokens.css`, lucide-react | Carried over |
| Tests and checks | pytest, pytest-django, pytest-cov (80% minimum), Hypothesis, Playwright screen check | Carried over |
| Deployment | Multi-stage Dockerfile, `central-station` Nginx, PowerShell deploy | Carried over |
| Background jobs | Second container from the same image, running a `worker` management command. Jobs sit in a Postgres table and are claimed with `FOR UPDATE SKIP LOCKED`. | New |
| Metadata read and write | ExifTool (Debian package `libimage-exiftool-perl`), kept running through PyExifTool | New |
| Image decoding | Pillow + pillow-heif | New |
| RAW decoding | Embedded JPEG preview via ExifTool first; rawpy (LibRaw) as the fallback | New |
| Video | ffprobe for metadata, ffmpeg for poster frames (Debian package) | New |
| Hashing | SHA-256 from `hashlib` for content; `imagehash` for 64-bit perceptual hashes | New |
| Photo grid | TanStack Virtual | New |
| Map | Leaflet, with supercluster for clustering | New |
| Place names | GeoNames city list loaded into Postgres | New |
| Faces (Phase 2) | ONNX Runtime with a face detection model and an embedding model; HDBSCAN from scikit-learn for clustering | New |
| Dropped | Recharts and TanStack Table, which nothing in the BRD needs | Removed |

I have not checked whether pillow-heif, rawpy, onnxruntime and scikit-learn publish wheels for Python 3.14. Confirm this before Phase 1a. If any lacks a wheel, the worker image can use an older Python while the web image stays on 3.14. Exact versions get pinned in `requirements.lock.txt` at that point.

## Deployment and environments

Production runs two containers from one image, `gallery-web` and `gallery-worker`, on the `vibe-city` network. Neither publishes a port. `central-station` serves the app at `gallery.localhost` and also mounts the library and thumbnail cache read-only, so it can stream files itself.

| Item | Development (Windows, native) | Production (Docker) |
| --- | --- | --- |
| Django | Port 8001 | `gallery-web`, Gunicorn. The entrypoint backs up the database (keeping 30), runs migrations, then starts. |
| Worker | `python manage.py worker` in a second terminal | `gallery-worker`, same image. It waits for migrations, then runs `manage.py worker`. |
| Frontend | Vite on 5173, forwarding `/api` and `/media` to 8001 | Built by the Node 22 stage, served by WhiteNoise |
| Postgres | `data-center-test` on 5433, database `gallery` | Production instance, database `gallery` |
| `LIBRARY_ROOT` | A sample copy, e.g. `D:\Photos-dev`, never the real library | Bind mount `D:\Photos` → `/library`: read-write for web and worker, read-only for Nginx |
| `CACHE_ROOT` | `.cache/` in the repo (git-ignored) | Named volume `gallery-cache` → `/cache`: read-write for web and worker, read-only for Nginx |
| Trash | `<LIBRARY_ROOT>/.gallery-trash/` | Same, inside the mount, so moves are instant renames |
| Media delivery | Django `FileResponse`, without Range support | `X-Accel-Redirect` to internal Nginx locations |
| URL | `localhost:5173` | `gallery.localhost` (vhost file in `docker/nginx/`) |

The settings screen (LIB-01) picks the library folder within `LIBRARY_ROOT`, which defaults to the root itself. A running container cannot see a folder outside its mount, so pointing at a different drive means editing the compose mount and redeploying.

Development must use a sample copy rather than the real library. This makes dev and prod sharing the library impossible, and dev bugs can only damage disposable files.

There is one `.env` at the repo root, as in financial-hub. The new keys are `LIBRARY_ROOT`, `CACHE_ROOT` and `MEDIA_DELIVERY` (`django` or `accel`).

Open question: does production Postgres share the instance financial-hub uses, with a separate database?

## Data model

The model separates the photo the owner sees (Asset) from the files on disk (MediaFile). Albums, tags, ratings and people attach to the Asset. That lets a RAW+JPEG pair share one record, and lets a merge simply move files under one Asset. Paths are stored relative to the library root in forward-slash form, so the same rows work with Windows paths in development and Linux paths in production.

| Django app | Entity | Key fields | Notes |
| --- | --- | --- | --- |
| library | Asset | kind (photo, video), capture\_at, capture\_offset, lat, lon, place, title, description, rating, favourite, primary\_file | Indexed on capture\_at for the timeline |
| library | MediaFile | asset, rel\_path (unique), role (primary, raw, paired\_jpeg), size, mtime, sha256, phash, width, height, mime, state (present, missing, trashed), last\_seen\_scan | `size` + `mtime` decide whether a rescan needs to re-hash |
| library | FileMetadata | file (one-to-one), raw ExifTool JSON (`jsonb`), extracted\_at | Normalised values live on Asset; the raw copy is kept for merges |
| library | ScanRun | started, finished, counts (new, changed, moved, missing, failed) | Feeds the scan status view (LIB-06) |
| organise | Album, AlbumItem | name, cover; album, asset, position | Many-to-many; files never move |
| organise | Tag, AssetTag | name (unique, case-insensitive); asset, tag |  |
| fileops | Operation | kind (move, rename, trash, restore, purge, write\_metadata, merge), state (pending, applied, undone, failed), parent, before / after (`jsonb`), created | The undo log. A merge is a parent with one child per step. |
| duplicates | DuplicateGroup, DuplicateMember | kind (exact, perceptual), state (open, merged, dismissed); group, file, distance, is\_proposed\_keeper | Dismissed groups are not re-raised |
| places | Place | geonames id, name, region, country, lat, lon | About 150,000 rows from the GeoNames city list |
| jobs | Job | kind, payload (`jsonb`), state, attempts, run\_after, locked\_at, error | The worker queue |
| core | AppSettings | library subfolder, exclusions, scan interval, trash days, write-back mode, phash threshold, auto-merge flag | Single row |
| people (Phase 2) | Face, Person | file, box, embedding (512 × float32 as `bytea`), person, ignored; name, cover\_face | At this scale no vector extension is needed |

## Indexing pipeline

Indexing is driven by rescans, not file watching. Each scan walks the folder, compares size and modification time with the database, and queues work only for files that are new or changed. Work runs in priority order, so the timeline becomes usable before the slower steps finish.

**Worker loop.**

- The worker claims jobs with `SELECT … WHERE state = 'queued' AND run_after <= now() ORDER BY priority, id FOR UPDATE SKIP LOCKED LIMIT n`.
- Several worker processes can run safely side by side. The default is CPU cores minus one.
- A failed job retries with backoff up to 3 times, then shows as failed in the scan status view.
- The worker also acts as the scheduler: it queues a scan every N minutes (from settings), and the "Rescan now" button queues one immediately.

**Scan, in two phases.**

1. **Walk.** `os.scandir` traverses the library, skipping exclusions and `.gallery-trash`. If a file's path, size and modification time all match the database, the scan only marks it as seen. Otherwise it queues `index_file`.
2. **Reconcile.** This runs once that scan's index jobs finish. A file not seen in the walk whose SHA-256 matches a newly found file counts as moved: its `rel_path` is updated and its Asset is kept (LIB-04). Any other unseen file is marked `missing`, never deleted, and drops out of views after two consecutive missed scans.

**Jobs by priority.**

| Priority | Job | Work |
| --- | --- | --- |
| 1 | `index_file` | Streams a SHA-256 of the file. Reads metadata with `exiftool -json -G -n`, stores the raw JSON, and normalises values onto the Asset. Pairs a RAW and JPEG with the same name in the same folder. Queues the jobs below. |
| 2 | `thumb_small` | Makes a 400 px WebP for the grid. |
| 3 | `thumb_large` | Makes a 2,048 px WebP for the viewer. Every format gets one, since browsers cannot show HEIC or RAW. |
| 4 | `phash` | Computes a 64-bit perceptual hash, for photos and RAW previews only. |
| 5 | `geocode` | Finds the nearest Place to the GPS position. |
| 6 | `dedupe` | Updates duplicate groups for the file (see the next section). |
| 7 | `faces` | Phase 2: detects faces and computes embeddings. |

**Thumbnail sources.**

- Pillow for common formats.
- pillow-heif for HEIC.
- The RAW's embedded JPEG preview, falling back to rawpy.
- For video, an ffmpeg frame taken at 10% of the duration.

EXIF orientation is applied before resizing. Thumbnails are stored at `cache/thumbs/ab/abcdef…_400.webp`, named by SHA-256. Because they are keyed by content, a moved file keeps its thumbnail, and byte-identical copies share one.

## Duplicate detection and merge

There are two detection paths. Exact matches come from a SQL grouping on SHA-256. Visual matches come from a pigeonhole index over 64-bit perceptual hashes, which avoids comparing all 100,000 photos with each other. Merges run as worker jobs inside a logged, reversible operation.

**Detection.**

- **Exact.** Any `sha256` shared by two or more present files forms an `exact` group. This applies to all media, including video, which is matched on exact copies only.
- **Perceptual.** Two photos match when the Hamming distance between their perceptual hashes is within the threshold. The default threshold is 4, and settings allow 0 to 7.
- **Perceptual index.** Each hash is split into 8 bytes. Two hashes within distance 7 must share at least one byte in the same position, so the worker keeps 8 in-memory buckets keyed by byte value. It compares a new file only against files in its buckets, then checks the full distance.
- **Excluded from groups:** files that belong to the same Asset (RAW+JPEG pairs).
- **Marked "similar" rather than duplicate:**
  - Two camera originals whose capture times differ by more than 2 seconds, such as burst shots.
  - Pairs whose aspect ratios differ by more than 1%, such as crops.
  - A copy whose `Software` tag names an editor.

**Merge, as one parent Operation with a child Operation per step:**

1. **Lock and re-check.** Lock the group and its files. If any file's size or modification time has changed on disk, abort and queue a rescan.
2. **Compute.** Build the merged metadata using the BRD rules. This is a pure function, tested with Hypothesis. Stop if any conflict is unresolved.
3. **Record.** Save the parent Operation as `pending`, with every file's path and raw metadata.
4. **Write.**
   - First copy the keeper's original bytes into `.gallery-trash/<op id>/keeper-original/`, so undo is exact.
   - Embedded formats: ExifTool writes to a temporary file in the same folder, the result is re-read and compared with the expected values, then `os.replace` swaps it in.
   - RAW and video: write a sidecar, using the same temporary-file-then-replace approach.
5. **Trash.** Move each non-keeper to `.gallery-trash/<op id>/<rel_path>`.
6. **Database.** In one transaction, move albums, tags and faces to the keeper's Asset, update the Asset fields, mark the trashed files, and set the group to `merged`.
7. **Finish.** Mark the parent `applied`.

If a step fails, the child operations already done are reversed in reverse order, and the parent is marked `failed`. If the worker crashes, it runs the same rollback on any `pending` parent when it next starts. Undo is that same rollback, applied to an `applied` operation.

## File operations and safety

Only the worker changes files. The web app validates each request, queues a job and returns an operation id, which the frontend polls with TanStack Query. That gives every file exactly one writer, and the same logging and rollback path covers every change.

**Path rules.** One function enforces these, tested with Hypothesis.

- The browser sends asset ids and folder paths relative to the library, never absolute paths.
- Every path is resolved and must pass `resolved.is_relative_to(library_root)`. Because resolving follows symlinks, a link pointing outside the library is rejected.
- Names are rejected if they contain `..`, a NUL character, the characters `< > : " | ? *` that NTFS does not allow, or Windows reserved names such as `CON` and `PRN`. The files live on NTFS even though the container is Linux.
- An operation never overwrites anything. If the target name exists, it gets a numbered suffix such as `  (1) `.

**Operations.**

- **Move and rename** use `os.replace` within the mount. A RAW+JPEG pair and its sidecars always move together.
- **Trash** moves the file to `.gallery-trash/<op id>/<rel_path>`. **Restore** moves it back to its original path, adding a suffix if that path is now taken.
- **Purge** is the only real deletion. A daily job removes trash folders older than the retention period. Their operations are then marked as no longer undoable.
- **Metadata edits** (ORG-04, MET-02) use write, verify, then replace. The operation log keeps the previous values, and undo writes them back.
- **Merges** go further and keep a full copy of the keeper's original bytes, as described in the previous section.
- **Organise by pattern** (FOP-04) first produces a dry-run list of source and target paths for the preview. Once confirmed, it runs as one parent operation.

## Media serving

Django decides whether a file may be served; Nginx does the actual sending. A media request reaches Django, which checks the session and looks up the file by id. It then hands Nginx an internal path through `X-Accel-Redirect`, and Nginx streams the bytes with Range support and `sendfile`. This keeps large videos off Gunicorn workers and makes video seeking work.

| URL | Serves | Caching |
| --- | --- | --- |
| `/media/thumb/<file id>/400?v=<sha>` | Grid WebP from the cache | `private, max-age=31536000, immutable`. The `v` value changes when the content changes. |
| `/media/preview/<file id>?v=<sha>` | 2,048 px viewer WebP, which is the default full view for every photo, HEIC and RAW | Same as above |
| `/media/original/<file id>` | The original file. Shown inline for JPEG, PNG, WebP, GIF and video; offered as a download for HEIC and RAW. | `private, no-cache` |

**Nginx (`docker/nginx/` vhost):**

- Two locations, `/_protected/library/` and `/_protected/cache/`, both marked `internal` and aliased to the read-only mounts. The browser cannot request them directly.
- Everything else is proxied to `gallery-web`, which serves the SPA and `/api`.

**In development**, `MEDIA_DELIVERY=django` streams through `FileResponse` instead. That has no Range support, so video seeking may not work in development, which is accepted.

**Two things to verify early:**

- File names with spaces, `#` and non-ASCII characters through `X-Accel-Redirect`. I'm not sure of Nginx's exact decoding behaviour here, so build the path with percent-encoding and test it.
- Whether the HEVC videos in the library play in the target browsers.

## Map and places

The map loads every geotagged point once and clusters them in the browser. Place names come from a local GeoNames table, so no photo location is ever sent out.

**Map view (MAP-01).**

- `GET /api/map/points` returns a compact array of `[asset id, lat, lon]`. At 100,000 points that is a few megabytes, cached by TanStack Query until the next scan.
- supercluster clusters the points in the browser, which stays fast at this size, and Leaflet draws the result.
- Selecting a cluster asks supercluster for its asset ids and opens them in the standard grid.

**Tiles.** These are the BRD's open question. There are two options:

- **Online OpenStreetMap tiles.** Simple and free for light personal use with attribution. The trade-off is that tile requests reveal which map areas are being viewed, though never the photos' exact positions.
- **A local PMTiles extract** served by Nginx from the cache volume, drawn with the Protomaps Leaflet layer. Fully private, but it needs a download of several gigabytes for the regions covered.

The design supports both through a tile-source setting. The default is online OSM until the owner decides.

**Place names (MET-03).**

- A management command loads the GeoNames `cities1000` file into Place. The file is downloaded once and kept in the repo's data folder.
- The `geocode` job looks for the nearest Place within a ±0.5° box using indexed lat/lon columns, ordered by distance, and widens the box if nothing is found.
- The job reruns whenever an Asset's GPS position changes, whether by edit or by merge.

## People (Phase 2)

Faces are detected and embedded on the CPU at the lowest job priority, then clustered once. After that, new faces are assigned to people the owner has already named, so confirmed groupings never get reshuffled.

**Pipeline.**

- The `faces` job runs on each photo's 2,048 px preview, which means RAW and HEIC need no extra decoding.
- A detection model finds face boxes and landmarks.
- Each face is aligned and cropped, and an embedding model produces a normalised 512-value vector, stored on Face.

**Model choice.** A short spike at the start of Phase 2 settles this, using about 500 of the owner's own photos with known people. The two candidates:

- **InsightFace's SCRFD + ArcFace models.** These are accurate, but I understand the pretrained weights are licensed for non-commercial use, so check the licence.
- **OpenCV's YuNet + SFace.** These have more permissive licences and are simpler to run, though likely less accurate.

**Clustering.**

- **First run:** HDBSCAN over all unassigned faces, with a minimum cluster size of 3. Unnamed clusters appear as "Unknown person" groups to name.
- **New faces:** compared with each named Person's average embedding and assigned when the similarity clears a threshold. Otherwise they go to the unassigned pool, and the pool is re-clustered nightly.
- **Owner actions:** name a cluster, merge two people, split by selecting faces, remove a face, or mark a detection as not a face. Removed and not-a-face detections are never re-suggested.
- **Scale:** 200,000 faces × 512 floats is about 400 MB. If HDBSCAN is too slow at that size, reduce the vectors to 64 dimensions with PCA before clustering.

**Write-back (PPL-05).** Names are written as MWG face regions through ExifTool, using the same write, verify and replace path, and logged for undo.

## API outline

The API is a DRF REST API under `/api`, using session auth and CSRF. Lists page by cursor on `(capture_at, id)`, which keeps deep timeline scrolling fast. Anything that changes files returns an operation id rather than waiting. The media URLs are covered under Media serving.

| Method and path | Purpose | Requirements |
| --- | --- | --- |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | Session sign-in | NFR-09 |
| `GET /api/assets?cursor=&filters…` | Timeline and filtered grid pages | BRW-01, BRW-06 |
| `GET /api/timeline/months` | Item counts per year and month, used for the scrubber and to size the virtual grid | BRW-01, BRW-02 |
| `GET /api/assets/{id}`, `PATCH /api/assets/{id}` | Detail with all metadata and files; edits queue a metadata write | BRW-05, ORG-02, ORG-04 |
| `POST /api/assets/bulk` | Bulk tag, rate, favourite and album changes | ORG-03 |
| `GET /api/folders?path=` | One level of the folder tree | BRW-03 |
| `GET /api/search?q=` | Text search | BRW-07 |
| `GET`, `POST /api/albums`; `PATCH`, `DELETE /api/albums/{id}`; `POST /api/albums/{id}/items` | Albums | ORG-01 |
| `POST /api/operations` | Move, rename, trash, restore and organise (with `dry_run`) | FOP-01 to FOP-04 |
| `GET /api/operations`, `GET /api/operations/{id}`, `POST /api/operations/{id}/undo` | Operation status, history and undo | FOP-03 |
| `GET /api/trash`, `POST /api/trash/purge` | Trash view and manual purge | FOP-02 |
| `GET /api/duplicates?kind=&state=` | Review queue | DUP-01 to DUP-03 |
| `GET /api/duplicates/{id}` | Members plus a merge preview: keeper, each field's value and source, and conflicts | DUP-04 |
| `POST /api/duplicates/{id}/merge`, `POST /api/duplicates/{id}/dismiss` | Merge with the keeper and conflict choices, or dismiss | DUP-05 |
| `POST /api/duplicates/merge-exact` | Bulk merge of conflict-free exact groups | DUP-06 |
| `GET /api/map/points` | Points for the map | MAP-01 |
| `GET /api/scan/status`, `POST /api/scan` | Scan progress, failures and rescan | LIB-03, LIB-06 |
| `GET`, `PUT /api/settings` | App settings | LIB-01 |
| `GET /api/people`, `PATCH /api/people/{id}`, `POST /api/people/merge`, `POST /api/faces/{id}/assign`, `POST /api/faces/{id}/ignore` | People (Phase 2) | PPL-02 to PPL-04 |

## Testing and operations

The testing is aimed mostly at proving that nothing on disk can be lost or corrupted. File tests always run against a throwaway copy of a small real-media corpus. Crash recovery is tested by failing each merge step on purpose.

**Test corpus.** This is a set of about 30 of the owner's own files, so there are no licensing concerns. It should include:

- A JPEG with full EXIF and GPS, plus a copy with metadata stripped and a resized copy
- A HEIC, a DNG, and a proprietary RAW from each camera brand owned
- A RAW+JPEG pair, two burst shots and a cropped edit
- An MP4 and an HEVC MOV

Each test copies the corpus into `tmp_path` first.

**Property tests with Hypothesis.**

- **Merge rules:** the merged result keeps every non-empty field from every copy unless it was a conflict. The result does not depend on the order of the copies, and merging twice changes nothing.
- **Path validator:** no generated string ever resolves outside the root.
- **Perceptual index:** it finds exactly the pairs a brute-force comparison finds within the threshold.

**Fault injection.** An exception is raised at each of the 7 merge steps, then the worker's recovery runs. The test passes only if the hashes of the file tree and a database snapshot match the state before the merge.

**Carried over from financial-hub:**

- 80% coverage gate
- `compose.test.yaml` throwaway Postgres
- The Playwright check, extended to sign in and screenshot the timeline, viewer, map, duplicate review, trash and settings against the development sample library

**Operations.**

- `deploy.ps1` builds one image and restarts `gallery-web` and `gallery-worker`.
- Only the web entrypoint runs backups and migrations.
- The scan status screen doubles as the health view, showing queue depth, failed jobs and the last scan time.
- The trash is not a backup. The owner's separate library backup remains the last line of defence.

## Key design decisions

Each decision below favours fewer moving parts and reversibility over speed. It can be revisited if real-library measurements in Phase 1a show it doesn't hold up.

| Decision | Alternative rejected | Why |
| --- | --- | --- |
| Job queue in a Postgres table with `SKIP LOCKED` | Celery + Redis | No new service to run, back up or monitor. The load (one user, up to 100,000 files) is well within what Postgres can handle. |
| Rescan by size and modification time | File watcher (inotify or watchdog) | Change events from Windows folders do not reliably reach a Linux container through a bind mount. |
| Index in place, folder stays the source of truth | Import or copy into managed storage | No second copy of the library, and other tools keep working on the same files. |
| Asset and MediaFile kept separate | One row per file | RAW+JPEG pairs, moves and merges keep albums, tags and people without copying them. |
| Only the worker writes files | Web requests write directly | One writer per file, and every change goes through the same logging and rollback path. |
| Trash inside the library folder | Trash in a separate volume | A move is an instant rename on the same drive and cannot be left half-copied. |
| Sidecars for proprietary RAW | Writing into RAW files | Undocumented formats risk corruption, and editors already read XMP sidecars. |
| Nginx `X-Accel-Redirect` for media | Django or Gunicorn streaming | Range requests for video seeking, and no Gunicorn worker held busy during long downloads. |
| Thumbnails keyed by SHA-256 | Keyed by path or id | Moves don't regenerate thumbnails, and identical copies share one. |
| Pigeonhole index for perceptual hashes | All-pairs comparison or a vector extension | Finds every match within the threshold, at near-linear cost, with no extension to install. |
| Development uses a sample copy of the library | Development on the real library | Development bugs can only damage disposable files, and two instances can never write to the same library. |
