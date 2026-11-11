/**
 * Timeline selection, shared by the grid and the selection bar in the top bar.
 * A tiny external store rather than context, so selecting a tile re-renders
 * only what reads the selection.
 */

import { useSyncExternalStore } from 'react'

interface SelectionState {
  readonly ids: ReadonlySet<string>
  /** The last item clicked: the start of a shift-click range. */
  readonly anchor: string | null
}

let state: SelectionState = { ids: new Set(), anchor: null }
const listeners = new Set<() => void>()

function set(next: SelectionState) {
  state = next
  listeners.forEach((l) => l())
}

export const selection = {
  get: () => state,
  subscribe(listener: () => void) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  clear() {
    if (state.ids.size || state.anchor) set({ ids: new Set(), anchor: null })
  },
  toggle(id: string) {
    const ids = new Set(state.ids)
    if (ids.has(id)) ids.delete(id)
    else ids.add(id)
    set({ ids, anchor: id })
  },
  add(ids: readonly string[], anchor: string | null) {
    set({ ids: new Set([...state.ids, ...ids]), anchor })
  },
  remove(ids: readonly string[], anchor: string | null) {
    const next = new Set(state.ids)
    ids.forEach((id) => next.delete(id))
    set({ ids: next, anchor })
  },
}

export function useSelection(): SelectionState {
  return useSyncExternalStore(selection.subscribe, selection.get)
}
