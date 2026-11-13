/** The top bar while timeline items are selected (ORG-03). */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { Icon } from '@/components/Icon'
import { api } from '@/lib/api'
import { bytes, count, plural } from '@/lib/format'
import { useAlbums, useAssets, useChange, useFolders, useTagSuggestions } from '@/lib/queries'
import { selection, useSelection } from '@/lib/selection'

type Pop = 'album' | 'tag' | 'rate' | 'move' | null

export function SelectionBar() {
  const [params] = useSearchParams()
  const albumId = params.get('album') ?? ''
  const assets = useAssets(params.get('q') ?? '', params.get('folder') ?? '', albumId, params.get('area') ?? '').data
  const { ids } = useSelection()
  const [pop, setPop] = useState<Pop>(null)
  const [tagDraft, setTagDraft] = useState('')
  const [albumDraft, setAlbumDraft] = useState<string | null>(null)
  const [folderDraft, setFolderDraft] = useState('')
  const albums = useAlbums().data ?? []
  const folders = useFolders().data ?? []
  const tags = useTagSuggestions().data ?? []
  const change = useChange()
  const bar = useRef<HTMLElement>(null)

  const selected = useMemo(() => (assets ?? []).filter((a) => ids.has(a.id)), [assets, ids])
  const list = [...ids]
  const n = list.length
  const items = `${count(n)} ${plural(n, 'item')}`
  const size = selected.reduce((sum, a) => sum + a.size + a.pairSize, 0)

  // A click anywhere outside an open popover closes it.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!(e.target instanceof Element) || !e.target.closest('.pop-anchor')) setPop(null)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [])

  const toggle = (p: Exclude<Pop, null>) => {
    setPop((cur) => (cur === p ? null : p))
    setTagDraft('')
    setAlbumDraft(null)
    setFolderDraft('')
  }

  const addToAlbum = (name: string) => {
    if (!name.trim()) return
    setPop(null)
    void change(() => api.addToAlbum(list, name.trim()), { msg: `Added ${items} to ${name.trim()}` })
  }
  const moveTo = (path: string) => {
    if (!path.trim()) return
    setPop(null)
    selection.clear()
    void change(() => api.move(list, path.trim()), { msg: `Moved ${count(n)} ${plural(n, 'file')} to ${path.trim()}` })
  }
  const removeFromAlbum = () => {
    selection.clear()
    void change(() => api.removeFromAlbum(albumId, list), { msg: `Removed ${items} from the album` })
  }

  const applyTag = (tag: string) => {
    if (!tag) return
    setPop(null)
    void change(() => api.addTag(list, tag), { msg: `Tagged ${items} “${tag}”` })
  }
  const rate = (stars: number) => {
    setPop(null)
    void change(() => api.updateAssets(list, { rating: stars }), {
      msg: stars ? `Rated ${items} ${'★'.repeat(stars)}` : `Cleared rating on ${items}`,
    })
  }
  const favourite = () => {
    const allFav = selected.length > 0 && selected.every((a) => a.favourite)
    void change(() => api.updateAssets(list, { favourite: !allFav }), {
      msg: allFav ? `Removed ${count(n)} from Favourites` : `Added ${count(n)} to Favourites`,
    })
  }
  const trash = () => {
    const only = selected[0]
    selection.clear()
    void change(() => api.trash(list), {
      msg: n === 1 && only ? `Moved ${only.name} to Trash` : `Moved ${items} to Trash`,
      detail: 'Kept for 30 days',
    })
  }

  return (
    <header className="topbar selecting" ref={bar}>
      <div className="sel-count">
        <b>{count(n)}</b>
        <span>selected</span>
        <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>{n ? `· ${bytes(size)}` : ''}</span>
      </div>

      <div className="pop-anchor">
        <button type="button" className="pill-btn ghost" onClick={() => toggle('album')}>
          <Icon name="album" size={16} />
          Add to album
        </button>
        {pop === 'album' && (
          <div className="popover" style={{ width: 250 }}>
            {albums.map((a) => (
              <button key={a.name} type="button" className="pop-item" onClick={() => addToAlbum(a.name)}>
                <Icon name="album" size={15} />
                <span className="grow">{a.name}</span>
                <span className="subtle num">{count(a.count)}</span>
              </button>
            ))}
            {albums.length > 0 && <div className="pop-sep" />}
            {albumDraft === null ? (
              <button type="button" className="pop-item accent" onClick={() => setAlbumDraft('')}>
                <Icon name="plus" size={15} />
                New album…
              </button>
            ) : (
              <input
                className="input"
                autoFocus
                placeholder="Album name, then Enter"
                value={albumDraft}
                onChange={(e) => setAlbumDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && addToAlbum(albumDraft)}
                style={{ minHeight: 32, fontSize: 'var(--text-sm)', margin: 4, width: 'calc(100% - 8px)' }}
              />
            )}
          </div>
        )}
      </div>

      <div className="pop-anchor">
        <button type="button" className="pill-btn ghost" onClick={() => toggle('tag')}>
          <Icon name="tag" size={16} />
          Tag
        </button>
        {pop === 'tag' && (
          <div className="popover tags">
            <input
              className="input"
              autoFocus
              placeholder="Add a tag and press Enter"
              value={tagDraft}
              onChange={(e) => setTagDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') applyTag(tagDraft.trim())
              }}
              style={{ minHeight: 32, fontSize: 'var(--text-sm)' }}
            />
            <div className="chips">
              {tags.map((t) => (
                <button key={t} type="button" className="tag-chip" onClick={() => applyTag(t)}>
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="pop-anchor">
        <button type="button" className="pill-btn ghost" onClick={() => toggle('rate')}>
          <Icon name="star" size={16} />
          Rate
        </button>
        {pop === 'rate' && (
          <div className="popover rate">
            {[1, 2, 3, 4, 5].map((stars) => (
              <button key={stars} type="button" className="star-btn" title={`${stars} ${plural(stars, 'star')}`} onClick={() => rate(stars)}>
                <Icon name="star" size={18} fill="currentColor" />
              </button>
            ))}
            <button type="button" className="pill-btn ghost" style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-muted)' }} onClick={() => rate(0)}>
              Clear
            </button>
          </div>
        )}
      </div>

      <button type="button" className="pill-btn ghost" onClick={favourite}>
        <Icon name="heart" size={16} />
        Favourite
      </button>

      <div className="pop-anchor">
        <button type="button" className="pill-btn ghost" onClick={() => toggle('move')}>
          <Icon name="folder-input" size={16} />
          Move
        </button>
        {pop === 'move' && (
          <div className="popover" style={{ width: 300 }}>
            <div className="caps pop-heading">Move files on disk to</div>
            <div className="pop-scroll">
              {folders.map((path) => (
                <button key={path} type="button" className="pop-item mono" onClick={() => moveTo(path)}>
                  <Icon name="folder" size={15} />
                  {path}
                </button>
              ))}
            </div>
            <input
              className="input mono"
              placeholder="New folder, e.g. 2024/Trip"
              value={folderDraft}
              onChange={(e) => setFolderDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && moveTo(folderDraft)}
              style={{ minHeight: 32, fontSize: 'var(--text-sm)', margin: 4, width: 'calc(100% - 8px)' }}
            />
            <div className="pop-sep" />
            <button type="button" className="pop-item" disabled title="Organise by date pattern (FOP-04) is next">
              <Icon name="folder-open" size={15} />
              <span className="grow">Organise into folders…</span>
              <span className="subtle" style={{ fontSize: 'var(--text-2xs)' }}>soon</span>
            </button>
          </div>
        )}
      </div>

      {albumId && (
        <button type="button" className="pill-btn ghost" onClick={removeFromAlbum}>
          <Icon name="x" size={16} />
          Remove from album
        </button>
      )}

      <button type="button" className="pill-btn ghost danger" onClick={trash}>
        <Icon name="trash" size={16} />
        Delete
      </button>

      <div className="grow" />

      <button type="button" className="pill-btn" onClick={() => selection.clear()}>
        <Icon name="x" size={15} />
        Clear
        <span className="subtle" style={{ fontSize: 'var(--text-2xs)', fontWeight: 600 }}>Esc</span>
      </button>
    </header>
  )
}
