import { NavLink, useLocation, useSearchParams } from 'react-router-dom'

import { Icon, Logo, type IconName } from '@/components/Icon'
import { bytes, count } from '@/lib/format'
import { useDuplicates, useLibrary, useTrashCount } from '@/lib/queries'
import type { Theme } from '@/lib/theme'

interface ItemProps {
  readonly to: string
  readonly icon: IconName
  readonly label: string
  readonly end?: boolean
  /** Overrides the router's own idea of active. */
  readonly active?: boolean
}

function Item({ to, icon, label, end, active }: ItemProps) {
  return (
    <NavLink
      to={to}
      end={end ?? false}
      className={({ isActive }) => `nav-item${(active ?? isActive) ? ' active' : ''}`}
    >
      <Icon name={icon} size={18} />
      <span>{label}</span>
    </NavLink>
  )
}

export function Sidebar({ theme, onToggleTheme }: { readonly theme: Theme; readonly onToggleTheme: () => void }) {
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  // A folder's photos are the timeline narrowed; they belong to Folders.
  const onTimelineRoute = pathname === '/' || pathname.startsWith('/photo/')
  const inFolder = onTimelineRoute && !!params.get('folder')
  const inAlbum = onTimelineRoute && !!params.get('album')
  const library = useLibrary().data
  const duplicates = useDuplicates().data
  const trash = useTrashCount().data

  const summary = !library ? '' : library.path ? `${count(library.itemCount)} items · ${bytes(library.totalBytes)}` : 'Not set up'

  return (
    <aside className="sidebar">
      <div className="brand">
        <Logo size={28} />
        <span className="display">The Gallery</span>
      </div>

      <nav className="nav-group">
        <Item to="/" icon="images" label="Timeline" active={onTimelineRoute && !inFolder && !inAlbum} />
        <Item to="/folders" icon="folder" label="Folders" active={pathname === '/folders' || inFolder} />
        <Item to="/albums" icon="album" label="Albums" active={pathname === '/albums' || inAlbum} />
        <Item to="/map" icon="map" label="Map" />
      </nav>

      <div className="nav-group">
        <div className="caps">Clean up</div>
        <NavLink to="/duplicates" className={({ isActive }) => `nav-item with-badge${isActive ? ' active' : ''}`}>
          <Icon name="copy" size={18} />
          <span className="grow">Duplicates</span>
          {!!duplicates?.total && <span className="nav-count">{count(duplicates.total)}</span>}
        </NavLink>
        <NavLink to="/trash" className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          <Icon name="trash" size={18} />
          <span className="grow">Trash</span>
          <span className="nav-meta">{trash !== undefined ? count(trash) : ''}</span>
        </NavLink>
        <NavLink to="/people" className={({ isActive }) => `nav-item dim${isActive ? ' active' : ''}`}>
          <Icon name="users" size={18} />
          <span className="grow">People</span>
          <span className="nav-phase">Phase 2</span>
        </NavLink>
      </div>

      <div className="grow" />

      <div className="nav-group">
        <Item to="/settings" icon="settings" label="Settings" />
        <div className="library-foot">
          <Icon name="drive" size={16} style={{ color: 'var(--color-text-subtle)' }} />
          <div className="lines">
            <span className="mono">{library?.path ?? 'No folder chosen'}</span>
            <span className="num">{summary}</span>
          </div>
          <button type="button" className="icon-btn sm" title="Switch theme" onClick={onToggleTheme}>
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
          </button>
        </div>
      </div>
    </aside>
  )
}
