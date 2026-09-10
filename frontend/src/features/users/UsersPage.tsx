import { useState } from 'react'
import { Plus, Trash2, Users as UsersIcon } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { Role, UserOut } from '../../lib/types'
import { useUsers } from './useUsers'
import { CreateUserModal } from './CreateUserModal'
import { Card } from '../../components/ui/Card'
import { Badge, type BadgeTone } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'

const ROLES: Role[] = ['admin', 'sre', 'consultant']

const ROLE_TONE: Record<Role, BadgeTone> = {
  admin: 'purple',
  sre: 'accent',
  consultant: 'neutral',
}

/**
 * Admin-only user management: list, create, inline role change, delete. Mirrors the shape of
 * other pages (`useDashboard`-style hook, `Skeleton`/`EmptyState`/`ErrorState`), but owns its own
 * page header since it isn't wired into `App.tsx`'s shared `PageHeader` action slot.
 */
export function UsersPage({ currentUserId }: { currentUserId: string | null }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [showCreate, setShowCreate] = useState(false)
  const [rowError, setRowError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const { users, loading, error, unreachable } = useUsers(refreshKey)
  const refresh = () => setRefreshKey((v) => v + 1)

  const changeRole = async (user: UserOut, role: Role) => {
    if (role === user.role) return
    setRowError(null)
    setBusyId(user.id)
    try {
      await api.patch<UserOut>(`/api/users/${user.id}`, { role })
      refresh()
    } catch (e) {
      setRowError(errText(e))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (user: UserOut) => {
    if (!confirm(`Delete ${user.email}? This cannot be undone.`)) return
    setRowError(null)
    setBusyId(user.id)
    try {
      await api.del(`/api/users/${user.id}`)
      refresh()
    } catch (e) {
      setRowError(errText(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="mb-3 flex items-center justify-end">
        <Button onClick={() => setShowCreate(true)}>
          <Plus size={16} /> New user
        </Button>
      </div>

      <div className="animate-in">
        {error ? (
          <ErrorState detail={error} unreachable={unreachable} onRetry={refresh} />
        ) : loading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((k) => (
              <Skeleton key={k} className="h-14 rounded-2xl" />
            ))}
          </div>
        ) : users.length === 0 ? (
          <EmptyState
            icon={UsersIcon}
            title="No users yet"
            hint="Create the first account so someone can sign in."
            action={
              <Button onClick={() => setShowCreate(true)}>
                <Plus size={16} /> New user
              </Button>
            }
          />
        ) : (
          <Card className="overflow-hidden">
            {rowError && (
              <p className="border-b border-hair px-5 py-2 text-xs text-sev-critical">{rowError}</p>
            )}
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hair text-xs font-semibold uppercase tracking-wide text-muted">
                  <th className="px-5 py-3">Email</th>
                  <th className="px-5 py-3">Role</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b border-hair last:border-0">
                    <td className="px-5 py-3 font-medium text-ink">{u.email}</td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <Badge tone={ROLE_TONE[u.role]}>{u.role}</Badge>
                        <select
                          aria-label={`Change role for ${u.email}`}
                          value={u.role}
                          disabled={busyId === u.id}
                          onChange={(e) => changeRole(u, e.target.value as Role)}
                          className="rounded-lg border border-hair bg-plane px-2 py-1 text-xs text-ink outline-none focus:border-accent disabled:opacity-50"
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button
                        variant="ghost"
                        disabled={busyId === u.id || u.id === currentUserId}
                        title={u.id === currentUserId ? "You can't delete your own account" : undefined}
                        onClick={() => remove(u)}
                      >
                        <Trash2 size={14} /> Delete
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>

      <CreateUserModal open={showCreate} onClose={() => setShowCreate(false)} onCreated={refresh} />
    </div>
  )
}
