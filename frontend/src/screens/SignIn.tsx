/** Single-account, password-only sign-in (NFR-09). */

import { useState, type FormEvent } from 'react'

import { Icon, Logo } from '@/components/Icon'
import { useLogin } from '@/lib/queries'

export function SignIn({ unreachable }: { readonly unreachable: string | null }) {
  const [password, setPassword] = useState('')
  const [empty, setEmpty] = useState(false)
  const login = useLogin()

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!password) {
      setEmpty(true)
      return
    }
    login.mutate(password, { onSettled: () => setPassword('') })
  }

  const error = empty ? 'Enter your password to continue.' : (login.error?.message ?? unreachable)

  return (
    <div className="app">
      <div className="signin" data-screen-label="Sign-in">
        <form onSubmit={submit}>
          <div className="brand">
            <Logo size={44} />
            <div className="display">The Gallery</div>
          </div>
          {/* The design names the library folder here. It is not asked for:
              where the library lives is for the signed-in owner only. */}
          <div className="muted">Your photo library, on this PC</div>
          <div className="field">
            <label htmlFor="pw">Password</label>
            <input
              id="pw"
              className="input"
              type="password"
              autoFocus
              autoComplete="current-password"
              placeholder="Enter password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value)
                setEmpty(false)
                login.reset()
              }}
            />
          </div>
          {error && (
            <div className="error" role="alert">
              <Icon name="alert" size={14} />
              {error}
            </div>
          )}
          <button className="btn btn-primary btn-block" type="submit" disabled={login.isPending}>
            Sign in
          </button>
          <div className="hint">
            Forgot it? Run <span className="mono">manage.py reset_password</span> on this PC.
          </div>
        </form>
      </div>
    </div>
  )
}
