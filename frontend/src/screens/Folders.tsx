/**
 * Folders (BRW-03): the folder tree on disk, one level at a time, shown as
 * album cards - the owner organises by folder, one per trip or period
 * (`180221 Penang`), so this is their albums view.
 *
 * Not in the Claude Design handoff (the sidebar linked to nothing); built from
 * the same tokens and the timeline's own pieces: the month heading, the tile
 * radius and badge, the hover ring.
 *
 * A card opens the timeline narrowed to that folder; a folder with subfolders
 * opens one level down instead, with "All photos" beside its heading.
 */

import { useNavigate, useSearchParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { count, plural } from '@/lib/format'
import { useFolderListing } from '@/lib/queries'
import type { FolderCard } from '@/lib/types'

const timelineFor = (path: string) => `/?${new URLSearchParams({ folder: path })}`
const listingFor = (path: string) => (path ? `/folders?${new URLSearchParams({ path })}` : '/folders')

export function Folders() {
  const [params] = useSearchParams()
  const path = params.get('path') ?? ''
  const navigate = useNavigate()
  const listing = useFolderListing(path)
  const data = listing.data
  // The top is the library folder (`base`), not the root of the drive.
  const atTop = !data || data.path === data.base
  const current = data?.path ?? path
  const parentPath = current.includes('/') ? current.slice(0, current.lastIndexOf('/')) : ''
  const parent = data && parentPath.length > data.base.length ? parentPath : ''

  const open = (f: FolderCard) => navigate(f.subfolders > 0 ? listingFor(f.path) : timelineFor(f.path))

  return (
    <div className="timeline" data-screen-label="Folders">
      <div className="folders">
        <div className="folders-head">
          {!atTop && (
            <button type="button" className="pill-btn back" onClick={() => navigate(listingFor(parent))}>
              <Icon name="arrow-left" size={16} />
              {parent ? parent.split('/').pop() : 'Folders'}
            </button>
          )}
          <span className="display">{atTop ? 'Folders' : (data?.title || current.split('/').pop())}</span>
          {data && (
            <span className="num">{`${count(data.folders.length)} ${plural(data.folders.length, 'folder')}`}</span>
          )}
          <div className="grow" />
          {!atTop && (
            <button type="button" className="pill-btn" onClick={() => navigate(timelineFor(current))}>
              <Icon name="images" size={15} />
              All photos
            </button>
          )}
        </div>

        {data && data.folders.length === 0 && (
          <p className="muted">
            {atTop ? 'No folders yet. Once a scan has found photos, their folders appear here.' : 'No subfolders here.'}
          </p>
        )}

        <div className="folder-grid">
          {data?.folders.map((f) => (
            <button key={f.path} type="button" className="folder-card" onClick={() => open(f)} title={f.name}>
              <div
                className="cover"
                style={f.coverUrl ? { backgroundImage: `url("${f.coverUrl}")` } : undefined}
              >
                {!f.coverUrl && <Icon name="folder" size={32} />}
                {f.subfolders > 0 && (
                  <span className="badge">
                    <Icon name="folder" size={11} />
                    {`${f.subfolders} ${plural(f.subfolders, 'folder')}`}
                  </span>
                )}
              </div>
              <div className="meta">
                <b>{f.title}</b>
                <span className="muted num">{f.span || ' '}</span>
                <span className="subtle num">{`${count(f.count)} ${plural(f.count, 'item')}`}</span>
              </div>
            </button>
          ))}
        </div>

        {data && data.looseCount > 0 && (
          <button type="button" className="pill-btn loose" onClick={() => navigate(atTop ? '/' : timelineFor(current))}>
            <Icon name="images" size={15} />
            {`${count(data.looseCount)} ${plural(data.looseCount, 'item')} not in a folder${atTop ? '' : ' below this one'}`}
          </button>
        )}
      </div>
    </div>
  )
}
