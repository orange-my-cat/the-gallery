/**
 * Let a screen put its selection controls in the top bar, the way the
 * timeline does (ORG-03): pinned above the scrolling content, in the accent
 * colour, with Escape to clear. While one is mounted the usual top bar steps
 * aside; when it unmounts (nothing selected, or the screen left) it returns.
 */

import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export const TAKEOVER_ID = 'topbar-takeover'

let claims = 0
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** True while a screen holds the top bar. */
export function useTopBarTaken(): boolean {
  return useSyncExternalStore(subscribe, () => claims > 0)
}

export function TopBarTakeover({ children, onEscape }: { readonly children: ReactNode; readonly onEscape?: () => void }) {
  const [target, setTarget] = useState<HTMLElement | null>(null)

  useLayoutEffect(() => {
    setTarget(document.getElementById(TAKEOVER_ID))
    claims += 1
    notify()
    return () => {
      claims -= 1
      notify()
    }
  }, [])

  useEffect(() => {
    if (!onEscape) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !(e.target instanceof HTMLInputElement)) onEscape()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onEscape])

  return target ? createPortal(<header className="topbar selecting">{children}</header>, target) : null
}
