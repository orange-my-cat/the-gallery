import { Outlet } from 'react-router-dom'

import type { Theme } from '@/lib/theme'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { TAKEOVER_ID } from './TopBarTakeover'

export function AppShell({ theme, onToggleTheme }: { readonly theme: Theme; readonly onToggleTheme: () => void }) {
  return (
    <div className="app">
      <Sidebar theme={theme} onToggleTheme={onToggleTheme} />
      <main className="main">
        <div id={TAKEOVER_ID} className="topbar-takeover" />
        <TopBar />
        <Outlet />
      </main>
    </div>
  )
}
