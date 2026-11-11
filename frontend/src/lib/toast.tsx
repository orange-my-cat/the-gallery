/** The undo toast: one at a time, bottom centre, gone after --duration-toast (8 s). */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { Icon } from '@/components/Icon'

interface ToastInput {
  readonly msg: string
  readonly detail?: string
  readonly undo?: () => void | Promise<void>
  readonly tone?: 'success' | 'error'
}
interface ToastState extends ToastInput {
  readonly id: number
}
interface ToastApi {
  show(toast: ToastInput): void
}

const ToastContext = createContext<ToastApi | null>(null)
const DURATION_MS = 8000

export function ToastProvider({ children }: { readonly children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const dismiss = useCallback(() => {
    window.clearTimeout(timer.current)
    setToast(null)
  }, [])

  const show = useCallback((input: ToastInput) => {
    window.clearTimeout(timer.current)
    const id = Date.now()
    setToast({ ...input, id })
    timer.current = window.setTimeout(() => setToast((t) => (t?.id === id ? null : t)), DURATION_MS)
  }, [])

  useEffect(() => () => window.clearTimeout(timer.current), [])
  const api = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={api}>
      {children}
      {toast && (
        <div className="toast" role="status">
          {toast.tone === 'error' ? (
            <Icon name="alert" size={18} style={{ color: 'var(--color-critical)' }} />
          ) : (
            <Icon name="circle-check" size={18} />
          )}
          <div className="msg">
            <b>{toast.msg}</b>
            {toast.detail && <span>{toast.detail}</span>}
          </div>
          {toast.undo && (
            <button
              type="button"
              className="undo"
              onClick={() => {
                void toast.undo?.()
                dismiss()
              }}
            >
              <Icon name="undo" size={15} />
              Undo
            </button>
          )}
          <button type="button" className="icon-btn" title="Dismiss" onClick={dismiss}>
            <Icon name="x" size={15} />
          </button>
        </div>
      )}
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
