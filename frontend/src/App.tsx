import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

import { demo } from './lib/api'
import { useSession } from './lib/queries'
import { useTheme } from './lib/theme'
import { AppShell } from './shell/AppShell'
import { Albums } from './screens/Albums'
import { DuplicateReview } from './screens/DuplicateReview'
import { Duplicates } from './screens/Duplicates'
import { Trash } from './screens/Trash'
import { Folders } from './screens/Folders'
import { NotBuilt } from './screens/NotBuilt'
import { People } from './screens/People'
import { Person } from './screens/Person'
import { Settings } from './screens/Settings'
import { SignIn } from './screens/SignIn'
import { Timeline } from './screens/Timeline'
import { Viewer } from './screens/Viewer'

// Leaflet is only needed on the map: loaded when it is first opened.
const MapView = lazy(() => import('./screens/MapView').then((m) => ({ default: m.MapView })))

export function App() {
  const session = useSession()
  const { theme, toggle } = useTheme()

  if (session.isPending) return <div className="app" />
  if (!session.data) return <SignIn unreachable={session.error?.message ?? null} />

  return (
    <Routes>
      <Route element={<AppShell theme={theme} onToggleTheme={toggle} />}>
        <Route path="/" element={<Timeline />}>
          <Route path="photo/:id" element={<Viewer />} />
        </Route>
        <Route path="/duplicates" element={<Duplicates />} />
        <Route path="/duplicates/:id" element={<DuplicateReview />} />
        <Route path="/folders" element={<Folders />} />
        <Route path="/albums" element={demo ? <NotBuilt icon="album" title="Albums" phase="1b" /> : <Albums />} />
        <Route
          path="/map"
          element={
            <Suspense fallback={<div className="map-screen" />}>
              <MapView />
            </Suspense>
          }
        />
        <Route path="/trash" element={demo ? <NotBuilt icon="trash" title="Trash" phase="1b" /> : <Trash />} />
        <Route path="/people" element={demo ? <NotBuilt icon="users" title="People" phase="2" /> : <People />} />
        <Route path="/people/:person" element={<Person />}>
          <Route path="photo/:id" element={<Viewer />} />
        </Route>
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
