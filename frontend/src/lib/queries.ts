/**
 * Server state through TanStack Query. A change is always an operation: it is
 * applied, the affected caches are invalidated, and a toast offers Undo for
 * as long as it is shown (FOP-03).
 */

import { useCallback, useEffect, useMemo } from 'react'
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'

import { api, ApiError, type OperationRef } from './api'
import { useToast } from './toast'
import type { Operation, ScanStatus, SettingsPatch } from './types'

export const keys = {
  session: ['session'] as const,
  library: ['library'] as const,
  assets: (q: string, folder: string, album: string, area: string) => ['assets', q, folder, album, area] as const,
  mapPoints: ['map-points'] as const,
  trashListing: ['trash-listing'] as const,
  operations: ['operations'] as const,
  allAssets: ['assets'] as const,
  folderListing: (path: string) => ['folder-listing', path] as const,
  allFolderListings: ['folder-listing'] as const,
  trash: ['trash-count'] as const,
  scan: ['scan'] as const,
  scanDetails: ['scan-details'] as const,
  settings: ['settings'] as const,
  albums: ['albums'] as const,
  folders: ['folders'] as const,
  tags: ['tag-suggestions'] as const,
  duplicates: ['duplicates'] as const,
  operation: (id: string) => ['operation', id] as const,
}

export function useSession() {
  return useQuery({
    queryKey: keys.session,
    queryFn: async () => {
      try {
        const state = await api.session()
        return state.authenticated ? state : null
      } catch (error) {
        if (error instanceof ApiError && error.isUnauthenticated) return null
        throw error
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (password: string) => api.login(password),
    onSuccess: (user) => client.setQueryData(keys.session, user),
  })
}

export const useLibrary = () => useQuery({ queryKey: keys.library, queryFn: api.library, staleTime: Infinity })
export const useTrashCount = () => useQuery({ queryKey: keys.trash, queryFn: api.trashCount })
export const useAlbums = () => useQuery({ queryKey: keys.albums, queryFn: api.albums, staleTime: Infinity })
export const useFolders = () => useQuery({ queryKey: keys.folders, queryFn: api.folders, staleTime: Infinity })
export const useTagSuggestions = () => useQuery({ queryKey: keys.tags, queryFn: api.tagSuggestions, staleTime: Infinity })
export const useDuplicates = () => useQuery({ queryKey: keys.duplicates, queryFn: api.duplicates })
/** Every geotagged point, fetched once and kept until something changes the library. */
export const useMapPoints = () => useQuery({ queryKey: keys.mapPoints, queryFn: api.mapPoints, staleTime: Infinity })


/**
 * The whole timeline for a search, newest first. Fetched a page at a time by
 * cursor and gathered into one list, because the justified layout needs every
 * item's shape. The first page renders straight away; the rest follow.
 */
export function useAssets(query: string, folder = '', album = '', area = '') {
  const result = useInfiniteQuery({
    queryKey: keys.assets(query, folder, album, area),
    queryFn: ({ pageParam }) => api.assetsPage(query, folder, album, area, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next,
    placeholderData: keepPreviousData,
  })
  const { hasNextPage, isFetchingNextPage, fetchNextPage, isError } = result

  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !isError) void fetchNextPage()
  }, [hasNextPage, isFetchingNextPage, isError, fetchNextPage])

  const data = useMemo(() => result.data?.pages.flatMap((p) => p.results), [result.data])
  return { data, isSuccess: result.isSuccess, isPending: result.isPending, error: result.error }
}

/**
 * How often, at most, a first scan reloads the timeline. Each reload refetches
 * the whole catalogue (megabytes, and nearly a second of server time for ten
 * thousand items), so it cannot follow the one-second status poll.
 */
const FIRST_SCAN_REFRESH_MS = 15_000
let firstScanRefresh = { at: 0, done: -1 }

export function useScanStatus() {
  const client = useQueryClient()
  return useQuery({
    queryKey: keys.scan,
    queryFn: async () => {
      const previous = client.getQueryData<ScanStatus>(keys.scan)
      const status = await api.scanStatus()
      // While a first scan runs, photos appear as they are found - when more
      // have been, and no more often than FIRST_SCAN_REFRESH_MS. When a scan
      // finishes, or the worker has caught up with thumbnails, what changed is
      // picked up at once.
      const finished = previous?.state === 'scanning' && status.state !== 'scanning'
      const caughtUp = (previous?.pending ?? 0) > 0 && status.pending === 0
      const now = Date.now()
      const found =
        status.state === 'scanning' &&
        status.firstScan &&
        status.done !== firstScanRefresh.done &&
        now - firstScanRefresh.at >= FIRST_SCAN_REFRESH_MS
      if (found || finished || caughtUp) {
        firstScanRefresh = { at: now, done: status.done }
        void invalidateLibrary(client)
        void client.invalidateQueries({ queryKey: keys.library })
      }
      return status
    },
    refetchInterval: (q) => {
      const s = q.state.data
      if (s?.state === 'scanning') return 1000
      return s && s.pending > 0 ? 3000 : 30_000
    },
  })
}

/** The scan pill's panel. Polls only while it is open. */
export const useScanDetails = (open: boolean) =>
  useQuery({ queryKey: keys.scanDetails, queryFn: api.scanDetails, enabled: open, refetchInterval: open ? 2000 : false })

export function useRetryFailed() {
  const client = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: api.retryFailed,
    onSuccess: ({ retried, status }) => {
      client.setQueryData(keys.scan, status)
      void client.invalidateQueries({ queryKey: keys.scanDetails })
      toast.show({ msg: `Trying ${retried} ${retried === 1 ? 'file' : 'files'} again`, detail: 'The worker picks them up now' })
    },
    onError: (error) => toast.show({ msg: 'Nothing was retried', detail: error.message, tone: 'error' }),
  })
}

export const useSettings = () => useQuery({ queryKey: keys.settings, queryFn: api.settings })

export const useBrowseFolders = (path: string, enabled: boolean) =>
  useQuery({
    queryKey: ['browse-folders', path],
    queryFn: () => api.browseFolders(path),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  })

export function useSaveSettings() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.saveSettings(patch),
    onSuccess: ({ settings, scanStarted }) => {
      client.setQueryData(keys.settings, settings)
      void client.invalidateQueries({ queryKey: keys.library })
      if (scanStarted) void client.invalidateQueries({ queryKey: keys.scan })
    },
  })
}

export function useStartScan() {
  const client = useQueryClient()
  const toast = useToast()
  return useMutation({
    mutationFn: api.startScan,
    onSuccess: (status) => client.setQueryData(keys.scan, status),
    onError: (error) => toast.show({ msg: 'The scan did not start', detail: error.message, tone: 'error' }),
  })
}

export const useFolderListing = (path: string) =>
  useQuery({ queryKey: keys.folderListing(path), queryFn: () => api.folderListing(path), placeholderData: keepPreviousData })

export function invalidateLibrary(client: QueryClient) {
  return Promise.all([
    client.invalidateQueries({ queryKey: keys.allAssets }),
    client.invalidateQueries({ queryKey: keys.allFolderListings }),
    client.invalidateQueries({ queryKey: keys.trash }),
    client.invalidateQueries({ queryKey: keys.trashListing }),
    client.invalidateQueries({ queryKey: keys.albums }),
    client.invalidateQueries({ queryKey: keys.tags }),
    client.invalidateQueries({ queryKey: keys.folders }),
    client.invalidateQueries({ queryKey: keys.operations }),
    client.invalidateQueries({ queryKey: keys.library }),
    client.invalidateQueries({ queryKey: keys.duplicates }),
    client.invalidateQueries({ queryKey: keys.mapPoints }),
  ])
}

const WORKING: readonly string[] = ['pending', 'undoing']

/**
 * Wait for an operation the worker is carrying out. Resolves with its final
 * state; gives up (as still running) after `timeoutMs`.
 */
export async function settled(operationId: string, timeoutMs = 120_000): Promise<Operation> {
  const until = Date.now() + timeoutMs
  let delay = 250
  for (;;) {
    const op = await api.operation(operationId)
    if (!WORKING.includes(op.state) || Date.now() > until) return op
    await new Promise((r) => setTimeout(r, delay))
    delay = Math.min(delay * 1.5, 1500)
  }
}

interface ChangeMessage {
  readonly msg: string
  readonly detail?: string
}

/**
 * Runs a change and offers Undo. A change the worker carries out (anything
 * that touches a file) is waited for, so the toast reports what actually
 * happened. Returns the operation id, or null when it failed - the failure is
 * shown and nothing is assumed saved.
 */
export function useChange() {
  const client = useQueryClient()
  const toast = useToast()

  return useCallback(
    async (run: () => Promise<OperationRef>, message: ChangeMessage) => {
      try {
        const ref = await run()
        // Database-only changes are already done; show them at once.
        if (ref.state === 'applied' || !ref.state) await invalidateLibrary(client)
        const op = ref.state && WORKING.includes(ref.state) ? await settled(ref.operationId) : ref
        await invalidateLibrary(client)
        if (op.state === 'failed') {
          toast.show({ msg: 'Nothing was changed', detail: op.error ?? 'The change could not be completed.', tone: 'error' })
          return null
        }
        toast.show({
          msg: message.msg,
          detail: message.detail ?? '',
          undo: async () => {
            try {
              const started = await api.undo(ref.operationId)
              const done = started && WORKING.includes(started.state) ? await settled(ref.operationId) : started
              await invalidateLibrary(client)
              if (done && done.state !== 'undone') {
                toast.show({ msg: 'Undo did not finish', detail: done.error ?? '', tone: 'error' })
              }
            } catch (error) {
              toast.show({ msg: 'Undo failed', detail: error instanceof Error ? error.message : String(error), tone: 'error' })
            }
          },
        })
        return ref.operationId
      } catch (error) {
        toast.show({ msg: 'Nothing was changed', detail: error instanceof Error ? error.message : String(error), tone: 'error' })
        return null
      }
    },
    [client, toast],
  )
}

export const useTrashListing = () => useQuery({ queryKey: keys.trashListing, queryFn: api.trashListing })

/** The activity list; polls while anything in it is still running. */
export const useOperations = (enabled: boolean) =>
  useQuery({
    queryKey: keys.operations,
    queryFn: api.operations,
    enabled,
    refetchInterval: (q) => (q.state.data?.some((op) => WORKING.includes(op.state)) ? 1000 : false),
  })
