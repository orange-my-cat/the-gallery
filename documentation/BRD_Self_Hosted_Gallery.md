# Self-Hosted Gallery — Business Requirements Document

Nov 8, 2026 · @Ivan

## Purpose and problem

The gallery is a single-user, self-hosted app. It lets the owner browse, organise and de-duplicate one local photo folder in place, without uploading anything or risking the original files.

The library is one folder on a local Windows drive. It holds photos, videos and camera RAW files collected over years from phones, cameras and backups. Three problems motivate the app:

- **Duplicates spread metadata across copies.** Repeated backups and messaging-app exports create several copies of one photo. One copy may have the GPS position, another the original capture date, and a third the keywords. No single copy is complete.
- **There is no single way to browse.** Finding a photo by date, place or person means digging through folders by hand.
- **Cloud galleries mean giving up privacy.** They require uploading private photos and locations to a third party.

The app reads the folder where it is, with no import or copy step. The folder stays the source of truth. Anything the app adds, such as albums, tags and merged metadata, is also written back to the files or to sidecar files wherever the format allows.

## Goals, success criteria and scope

Success means three things. Every photo, video and RAW file in the folder can be found by date, place or album. Every duplicate group can be collapsed into one complete copy. No original is ever lost without the owner purging it from trash.

| Goal | Success criterion |
| --- | --- |
| Find anything | Every indexed item is reachable from the timeline, folder view, map or an album. The timeline scrolls smoothly at 100,000 items. |
| One complete copy per photo | 100% of byte-identical copies are detected. After a merge, the keeper holds every non-empty metadata field that any copy had, apart from conflicts the owner resolved. |
| No accidental loss | Every delete, move, rename and merge can be undone until the trash is purged. Zero originals are lost to app bugs in testing. |
| Stay private | No photo, metadata or location leaves the PC. |
| Low upkeep | The app uses the same stack, deployment and test conventions as the existing financial-hub app. |

**In scope, Phase 1:**

- Indexing of photos, videos and camera RAW
- Timeline, folder and map views
- Albums, tags, favourites and ratings
- File operations: move, rename, delete to trash, organise into folders
- Duplicate detection and metadata merge
- Metadata write-back to the files

**In scope, Phase 2:**

- Face recognition and people

**Out of scope:**

- Multiple users
- Share links
- Access from other devices or over the internet
- AI smart search
- Pixel editing such as crop or filters
- Phone upload or sync
- Cloud backup
- More than one library folder

## User and usage context

There is one user, the owner, working in a desktop browser on the same Windows PC that holds the library. The app is reached at `gallery.localhost` and is never exposed on the home network or the internet.

The app still requires a sign-in, with one account and session login. It can delete and move files, so a stray browser tab or another local app should not be able to trigger that without a session. This also matches the existing financial-hub app.

The main usage scenarios:

1. **After a phone or camera backup**, the owner runs a rescan, reviews the new items in the timeline, and merges any duplicates the backup created.
2. **To find a photo**, the owner jumps to a year and month, filters by tag or camera, or browses the map.
3. **To organise**, the owner multi-selects items, then adds them to albums and tags them. Optionally, the owner moves them into a dated folder structure.
4. **For a clean-up session**, the owner works through the duplicate review queue, resolves any conflicts, and later empties the trash.
5. **In Phase 2**, the owner names the face clusters the app has found, then browses by person.

## Functional requirements

The requirements are grouped by area:

- **LIB:** library and indexing
- **BRW:** browsing
- **ORG:** organisation
- **FOP:** file operations
- **DUP:** duplicates
- **MET:** metadata
- **MAP:** map
- **PPL:** people

PPL is Phase 2. Every other area is Phase 1.

| ID | Requirement | Priority |
| --- | --- | --- |
| LIB-01 | A settings screen sets the library folder, excluded subfolders, scan schedule, trash retention, metadata write-back mode and duplicate sensitivity. | Must |
| LIB-02 | Indexes photos (JPEG, PNG, HEIC/HEIF, WebP, TIFF, GIF), camera RAW (CR2, CR3, NEF, ARW, RAF, ORF, RW2, DNG) and video (MP4, MOV, M4V, MKV, AVI, 3GP). | Must |
| LIB-03 | Rescans on a schedule (default hourly) and on demand, detecting new, changed, moved and removed files. | Must |
| LIB-04 | A file moved or renamed outside the app keeps its albums, tags, people and merged metadata. | Must |
| LIB-05 | A RAW and a JPEG with the same name in the same folder show as one item. | Must |
| LIB-06 | Shows scan progress, queue size and files that failed to read. | Should |
| BRW-01 | A timeline shows all items by capture date, newest first, grouped by month and day, and scrolls smoothly at 100,000 items. | Must |
| BRW-02 | A scrubber jumps to any year and month. | Should |
| BRW-03 | A folder view mirrors the folder tree on disk. | Should |
| BRW-04 | A viewer shows full-size images, shows RAW via its embedded preview, plays video with seeking, and supports next, previous and zoom. | Must |
| BRW-05 | An info panel shows all metadata: date, camera, lens, exposure, GPS, place, path, size and dimensions. | Must |
| BRW-06 | Filters cover date range, media type, camera, album, tag, rating, favourite and has-location. | Should |
| BRW-07 | Text search covers file name, tags, album, title, description and place name. | Should |
| BRW-08 | An "On this day" view shows items from the same date in earlier years. | Could |
| ORG-01 | Albums group items without moving files, and one item can be in many albums. | Must |
| ORG-02 | Items can have tags, a favourite flag and a 0–5 rating. | Must |
| ORG-03 | Multi-select allows bulk add to album, tag, rate, move and delete. | Must |
| ORG-04 | The owner can edit an item's date, title, description and location, including placing it on the map. | Should |
| FOP-01 | The owner can move and rename files and folders inside the library. | Must |
| FOP-02 | Delete moves items to a trash folder inside the library. Items can be restored from a trash view, and are purged after the retention period (default 30 days) or on demand. | Must |
| FOP-03 | Every file operation and metadata write is logged and can be undone until the trash is purged. | Must |
| FOP-04 | Selected items can be organised into a folder pattern such as `{YYYY}/{MM}` by capture date, with a preview before anything moves. | Should |
| DUP-01 | Detects byte-identical copies of any media type. | Must |
| DUP-02 | Detects visually identical photos whose bytes differ (resized, re-encoded or metadata stripped), with adjustable sensitivity. | Must |
| DUP-03 | Never treats RAW+JPEG pairs as duplicates. Burst shots and edited versions show as "similar" only. | Must |
| DUP-04 | A review screen shows the copies side by side, the proposed keeper, and each merged field with its source copy and any conflict. | Must |
| DUP-05 | A merge keeps one file and combines metadata by the merge rules. It moves the other copies to trash and transfers their albums, tags and people to the keeper. | Must |
| DUP-06 | Byte-identical groups with no conflicts can be bulk-merged, if switched on in settings. | Should |
| MET-01 | Reads all EXIF, IPTC, XMP and video metadata and keeps a raw copy of it. | Must |
| MET-02 | Writes edits and merged metadata back to disk: embedded XMP for JPEG, HEIC, TIFF, PNG and DNG, and an XMP sidecar for proprietary RAW and video. | Must |
| MET-03 | Turns GPS positions into place names without an online service. | Should |
| MAP-01 | A map shows all geotagged items in clusters. Selecting a cluster opens its items in a grid. | Must |
| MAP-02 | The info panel shows a small map for one item. | Should |
| PPL-01 | Detects faces in photos on the PC, with no cloud service. | Must |
| PPL-02 | Groups faces into people. The owner names, merges and splits groups. | Must |
| PPL-03 | The owner can browse and filter by one or more people. | Must |
| PPL-04 | The owner can remove a wrong face from a person, or mark a detection as not a face. | Should |
| PPL-05 | Writes person names to the files as XMP face regions. | Could |

## Duplicate merge rules

A merge keeps the best-quality file as the keeper. It fills that file with every non-empty metadata value from all copies, and only asks the owner when copies genuinely disagree.

**Keeper selection.** The rules apply in order, and the owner can override the result on the review screen.

1. Most pixels.
2. With equal pixels, the copy that still has camera-original metadata (Make, Model and DateTimeOriginal present). This signals it was not re-exported.
3. Then the larger file, which means less compression.
4. Then the copy already inside the organised folder pattern, if one is set.
5. Then the oldest file in the library.

**Field rules:**

| Field | Merged value | Conflict raised when |
| --- | --- | --- |
| Capture date and time zone offset | Taken from the camera-original copy. Otherwise, the earliest DateTimeOriginal. Otherwise, CreateDate. File modified time is used only when no copy has any of these. | Copies differ by more than 2 seconds. Exception: a whole-hour difference where only one copy has an offset is resolved using that offset. |
| GPS position and altitude | From any copy that has it. If several have it, from the camera-original copy. | Positions are more than 100 m apart. |
| Camera, lens and exposure | From the copy that has them. | Values differ. |
| Dimensions and orientation | Always the keeper's own. | Never. |
| Keywords and tags | All copies combined, duplicates removed ignoring case. | Never. |
| Albums and favourite | All copies combined. Favourite if any copy is a favourite. | Never. |
| Rating | Highest. | Never. |
| Title and description | The single non-empty value. | Copies have different non-empty values. |
| People and face regions (Phase 2) | All copies combined. Regions covering the same face are merged. | One face is named as two different people. |
| Place name | Recalculated from the merged GPS position. | Never. |
| All other tags | The keeper's own, with gaps filled from the other copies. | Never. |

A merge with any open conflict waits until the owner picks a value for each one. The screen also offers "use the keeper's value" for all conflicts at once.

A merge counts as complete only when all four of these have happened:

- The merged metadata is written to the keeper, embedded or as a sidecar.
- The database is updated.
- The other copies are in the trash.
- The operation is logged.

If any step fails, all of them are rolled back.

## Non-functional requirements

Data safety outranks every other quality. Where a requirement trades speed against safety, safety wins.

| ID | Area | Requirement | Target |
| --- | --- | --- | --- |
| NFR-01 | Data safety | Originals are never permanently deleted except by a trash purge. | 0 originals lost in testing |
| NFR-02 | Data safety | Metadata writes go to a temporary copy, are checked by re-reading, then replace the original in one atomic rename. | No half-written files |
| NFR-03 | Data safety | Each file change and its database change succeed or fail together. A crash mid-operation is recovered from the operation log on restart. | 100% of operations recoverable |
| NFR-04 | Performance | First full index of 100,000 files, covering metadata, hashes and thumbnails. | Overnight, 12 hours or less |
| NFR-05 | Performance | Rescan of an unchanged library. | Under 5 minutes |
| NFR-06 | Performance | Timeline shows its first thumbnails after opening. | Under 1 second |
| NFR-07 | Performance | Viewer shows a full-size image or starts a video. Video seeking works. | Under 2 seconds |
| NFR-08 | Privacy | No photo, metadata or location is sent off the PC. Face detection and reverse geocoding run locally. | 0 outbound requests carrying user data |
| NFR-09 | Security | Reachable only at `gallery.localhost` on this PC, behind a single-account session login with CSRF protection. | Not reachable from other devices |
| NFR-10 | Recoverability | The database is backed up on every start, keeping the last 30 backups. Thumbnails are excluded because they can be rebuilt. | Restore tested once per phase |
| NFR-11 | Maintainability | Same stack, layout and conventions as financial-hub. pytest coverage stays at 80% or more, with Hypothesis tests for merge rules and path handling. | CI gate |
| NFR-12 | Compatibility | Windows 11 host with Docker Desktop, on current Chrome, Edge and Firefox. | Playwright check passes on each |

## Assumptions, risks and phasing

The biggest risk is the app damaging originals. For that reason, the merge feature ships only after trash, undo and the operation log have been proven.

**Assumptions**

- The library is one folder on one local drive and holds up to 100,000 files.
- There is one user, working on this PC only.
- The development and production instances never run against the library at the same time.
- The owner keeps a separate backup of the library folder, outside the app.

**Constraints**

- The stack is fixed to the financial-hub stack.
- The container sees the library only through a bind mount.
- Windows bind mounts do not deliver file-change events to the container, so change detection relies on rescans.
- Proprietary RAW files are never written to.

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| A bug in file operations deletes or corrupts originals | High | Trash instead of delete, operation log with undo, write-and-verify, and tests against a scratch copy of sample media. Back up the library before the first merge. |
| A perceptual-hash match merges two different photos | High | Only byte-identical groups can bulk-merge. Every other match needs review, uses a conservative default threshold, and can be undone. |
| Scanning through a Docker Desktop bind mount is slow at 100,000 files | Medium | Measure early with the real library. The fallback is to run the worker natively on Windows. |
| Face libraries have no wheels for Python 3.14 yet | Medium | Check before Phase 2. The fallback is a separate worker image on an older Python. |
| HEVC videos do not play in some browsers | Medium | Test with real files. If needed, add H.264 preview transcoding. |

**Open questions**

- [ ] Is a 30-day trash retention right?
- [ ] Which editor should read the RAW sidecars? Lightroom names them `IMG_1234.xmp`, while darktable uses `IMG_1234.CR3.xmp`.
- [ ] Should map tiles come from a local offline file (fully private), or from online OpenStreetMap tiles (simpler, but reveals the map areas being viewed)?
- [ ] Is a separate backup of the library folder already in place?

**Phasing**

1. **1a, Index and browse:** LIB, BRW and MET-01, read-only. This proves scanning speed with the real library.
2. **1b, Organise:** ORG, FOP and MET-02. Trash, undo and the operation log are proven here.
3. **1c, Duplicates:** all DUP requirements, including merge.
4. **1d, Map:** MAP and MET-03.
5. **2, People:** all PPL requirements.
