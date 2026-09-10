import { useEffect, useState } from 'react'
import { api, errText, isUnreachable } from '../../lib/api'
import type { UserOut } from '../../lib/types'

export interface UsersData {
  users: UserOut[]
  loading: boolean
  error: string | null
  unreachable: boolean
}

/**
 * One shared fetch of `GET /api/users`, keyed by `refreshKey` — mirrors `useDashboard.ts`'s
 * pattern so the Users page bumps a version after create/edit/delete rather than re-fetching ad
 * hoc.
 */
export function useUsers(refreshKey: number): UsersData {
  const [users, setUsers] = useState<UserOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    api
      .get<UserOut[]>('/api/users')
      .then((u) => {
        if (!alive) return
        setUsers(u)
        setError(null)
      })
      .catch((e) => {
        if (!alive) return
        setError(errText(e))
        setUnreachable(isUnreachable(e))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [refreshKey])

  return { users, loading, error, unreachable }
}
