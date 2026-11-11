/**
 * Albums (ORG-01): groups of items that cross folders, without moving a file.
 *
 * Not in the Claude Design handoff; the same card grid as Folders. A card
 * opens the timeline narrowed to the album. Rename and delete are on each
 * card; deleting an album never deletes a photo, and it can be undone.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { count, plural } from '@/lib/format'
import { useAlbums, useChange } from '@/lib/queries'
import type { AlbumSummary } from '@/lib/types'

export function Albums() {
  const albums = useAlbums().data
  const change = useChange()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null)

  const create = () => {
    const name = draft?.trim()
    setDraft(null)
    if (name) void change(() => api.createAlbum(name), { msg: `Created the album ${name}` })
  }
  const rename = () => {
    if (!renaming) return
    const { id, name } = renaming
    setRenaming(null)
    if (name.trim()) void change(() => api.renameAlbum(id, name.trim()), { msg: `Renamed to ${name.trim()}` })
  }
  const remove = (a: AlbumSummary) => {
    if (a.id) void change(() => api.deleteAlbum(a.id as string), { msg: `Deleted the album ${a.name}`, detail: 'Its photos stay in the library' })
  }

  return (
    <div className="timeline" data-screen-label="Albums">
      <div className="folders">
        <div className="folders-head">
          <span className="display">Albums</span>
          {albums && <span className="num">{`${count(albums.length)} ${plural(albums.length, 'album')}`}</span>}
          <div className="grow" />
          {draft === null ? (
            <button type="button" className="pill-btn" onClick={() => setDraft('')}>
              <Icon name="plus" size={15} />
              New album
            </button>
          ) : (
            <input
              className="input"
              autoFocus
              placeholder="Album name, then Enter"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') create()
                if (e.key === 'Escape') setDraft(null)
              }}
              onBlur={() => setDraft(null)}
              style={{ width: 260 }}
            />
          )}
        </div>

        {albums && albums.length === 0 && (
          <p className="muted">
            No albums yet. Select photos on the timeline and choose Add to album, or make one here. Albums never move
            or copy a file.
          </p>
        )}

        <div className="folder-grid">
          {albums?.map((a) => (
            <div key={a.id ?? a.name} className="folder-card album-card">
              <button type="button" className="cover-btn" onClick={() => a.id && navigate(`/?album=${a.id}`)} title={a.name}>
                <div className="cover" style={a.coverUrl ? { backgroundImage: `url("${a.coverUrl}")` } : undefined}>
                  {!a.coverUrl && <Icon name="album" size={32} />}
                </div>
              </button>
              <div className="meta">
                {renaming?.id === a.id ? (
                  <input
                    className="input"
                    autoFocus
                    value={renaming?.name ?? ''}
                    onChange={(e) => setRenaming({ id: a.id as string, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') rename()
                      if (e.key === 'Escape') setRenaming(null)
                    }}
                    onBlur={() => setRenaming(null)}
                  />
                ) : (
                  <b>{a.name}</b>
                )}
                <span className="subtle num">{`${count(a.count)} ${plural(a.count, 'item')}`}</span>
              </div>
              {a.id && (
                <div className="card-actions">
                  <button type="button" className="icon-btn sm" title="Rename" onClick={() => setRenaming({ id: a.id as string, name: a.name })}>
                    <Icon name="pencil" size={14} />
                  </button>
                  <button type="button" className="icon-btn sm" title="Delete album (photos stay)" onClick={() => remove(a)}>
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
