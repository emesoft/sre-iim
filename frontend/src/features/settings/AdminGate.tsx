import { type ReactNode, useState } from 'react'
import { api, errText, getAdminToken, setAdminToken } from '../../lib/api'
import type { AdminLoginResponse } from '../../lib/types'
import { Button } from '../../components/ui/Button'

/**
 * Gates its children behind a single shared admin password (not a per-user account system) —
 * the Settings page (cloud connections, Claude Code token) is admin-only. See
 * backend/app/interface/http/auth.py.
 */
export function AdminGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => getAdminToken() !== null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (authed) return <>{children}</>

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const { token } = await api.post<AdminLoginResponse>('/api/auth/admin-login', { password })
      setAdminToken(token)
      setAuthed(true)
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-3 rounded-2xl border border-hair bg-surface p-6"
      >
        <h2 className="text-lg font-semibold text-ink">Admin sign-in required</h2>
        <p className="text-sm text-muted">Settings is admin-only.</p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Admin password"
          className="rounded-lg border border-hair bg-plane p-2"
          autoFocus
          required
        />
        {error && <p className="text-sm text-sev-critical">{error}</p>}
        <Button type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </div>
  )
}
