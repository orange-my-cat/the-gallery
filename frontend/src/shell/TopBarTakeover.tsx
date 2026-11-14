/**
 * Let a screen put its selection controls in the top bar, the way the
 * timeline does (ORG-03): pinned above the scrolling content, in the accent
 * colour, with Escape to clear. While one is mounted the usual top bar steps
 * aside; when it unmounts (nothing selected, or the screen left) it returns.
 */

import { useEffect, useLayoutEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { Icon } from '@/components/Icon'
import { count } from '@/lib/format'

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

export function TopBarTakeover({ children, onEscape }: { readonly children: ReactNode; readonly onEscape?: (() => void) | undefined }) {
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

/**
 * The left of every selection bar: how many are selected, then Clear (Esc).
 * It ends with a spacer, so whatever follows - the actions - sits on the right.
 */
export function SelectionHead({ n, onClear, detail }: { readonly n: number; readonly onClear: () => void; readonly detail?: string | undefined }) {
  return (
    <>
      <div className="sel-count">
        <b>{count(n)}</b>
        <span>selected</span>
        {detail && <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>{`· ${detail}`}</span>}
      </div>
      <button type="button" className="pill-btn" onClick={onClear}>
        <Icon name="x" size={15} />
        Clear
        <span className="subtle" style={{ fontSize: 'var(--text-2xs)', fontWeight: 600 }}>Esc</span>
      </button>
      <div className="grow" />
    </>
  )
}
