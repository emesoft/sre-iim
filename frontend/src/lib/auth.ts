import { useEffect, useState } from 'react'
import { api, clearAuthToken, errText, getAuthToken, setAuthToken } from './api'
import type { LoginResponse, Role, UserOut } from './types'

/**
 * Real per-user auth: POSTs to `/api/auth/login`, stores the returned `{ token, user }` pair, and
 * attaches the token to every subsequent request via `authHeaders()` in `api.ts`. "Remember me"
 * still picks the store: localStorage (survives restarts) vs sessionStorage (cleared when the tab
 * closes) — the token and the cached user object always live in the same store.
 */
const USER_KEY = 'iim-auth-user'

function readUser(): UserOut | null {
  const raw = localStorage.getItem(USER_KEY) ?? sessionStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as UserOut
  } catch {
    return null
  }
}

export function useAuth() {
  const [user, setUser] = useState<UserOut | null>(() => (getAuthToken() ? readUser() : null))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // If the stored token has expired (or the user was deleted), a 401 from any call clears the
  // token via api.ts's handle(); reconcile our local user state on the next render.
  useEffect(() => {
    if (!getAuthToken() && user) setUser(null)
  })

  const signIn = async (email: string, password: string, remember = true) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.post<LoginResponse>('/api/auth/login', { email, password })
      setAuthToken(res.token, remember)
      const value = JSON.stringify(res.user)
      ;(remember ? sessionStorage : localStorage).removeItem(USER_KEY)
      ;(remember ? localStorage : sessionStorage).setItem(USER_KEY, value)
      setUser(res.user)
    } catch (e) {
      setError(errText(e))
      throw e
    } finally {
      setLoading(false)
    }
  }

  const signOut = () => {
    clearAuthToken()
    localStorage.removeItem(USER_KEY)
    sessionStorage.removeItem(USER_KEY)
    setUser(null)
  }

  const role: Role | null = user?.role ?? null

  return { authed: user !== null, user, role, signIn, signOut, loading, error }
}
