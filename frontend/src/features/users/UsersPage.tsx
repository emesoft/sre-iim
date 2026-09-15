import { useEffect, useState } from 'react'
import { KeyRound, Plus, Settings2, Trash2, Users as UsersIcon } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { Group, Project, UserOut } from '../../lib/types'
import { useUsers } from './useUsers'
import { CreateUserModal } from './CreateUserModal'
import { ResetPasswordModal } from './ResetPasswordModal'
import { GroupsModal } from './GroupsModal'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { PageHeader } from '../../components/layout/PageHeader'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'

/**
 * Admin-only user management: accounts and the group each one belongs to.
 *
 * One column, because it's one decision. A group carries the permission level *and* the projects,
 * so "SRE · gcm" says everything there is to say about what an account can do — and the line
 * underneath spells out the consequence so nobody has to hold the mapping in their head.
 */
export function UsersPage({ currentUserId }: { currentUserId: string | null }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [showCreate, setShowCreate] = useState(false)
  const [resetTarget, setResetTarget] = useState<UserOut | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [groups, setGroups] = useState<Group[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [showGroups, setShowGroups] = useState(false)
  const { users, loading, error, unreachable } = useUsers(refreshKey)
  const refresh = () => setRefreshKey((v) => v + 1)

  const adminCount = users.filter((u) => u.role === 'admin').length

  useEffect(() => {
    api.get<Group[]>('/api/groups').then(setGroups).catch(() => setGroups([]))
    api.get<Project[]>('/api/projects').then(setProjects).catch(() => setProjects([]))
  }, [refreshKey])

  const assignGroup = async (user: UserOut, groupId: string | null) => {
    setRowError(null)
    setBusyId(user.id)
    try {
      await api.put<UserOut>(`/api/users/${user.id}/group`, { group_id: groupId })
      refresh()
    } catch (e) {
      // Includes the backend's last-admin refusal, which the UI can't fully predict.
      setRowError(errText(e))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (user: UserOut) => {
    if (!confirm(`Delete ${user.username}? This cannot be undone.`)) return
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
    <div className="flex h-full flex-col">
      <PageHeader
        title="Users"
        subtitle="Accounts, their role, and the projects each one can see"
        action={
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setShowGroups(true)}>
              <Settings2 size={16} /> Groups
            </Button>
            <Button onClick={() => setShowCreate(true)}>
              <Plus size={16} /> New user
            </Button>
          </div>
        }
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-10 md:px-8">
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
                <p className="border-b border-hair px-5 py-2 text-xs text-sev-critical">
                  {rowError}
                </p>
              )}
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hair text-xs font-semibold uppercase tracking-wide text-muted">
                    <th className="px-5 py-3">User</th>
                    <th className="px-5 py-3">Sign-in</th>
                    <th className="px-5 py-3">Group</th>
                    <th className="px-5 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isSelf = u.id === currentUserId
                    const isLastAdmin = u.role === 'admin' && adminCount <= 1
                    const isEntra = u.auth_provider === 'entra'
                    return (
                      <tr key={u.id} className="border-b border-hair last:border-0">
                        <td className="px-5 py-3">
                          <div className="font-medium text-ink">
                            {u.username}
                            {isSelf && <span className="ml-1.5 text-xs text-muted">(you)</span>}
                          </div>
                          <div className="text-xs text-muted">{u.email || '—'}</div>
                        </td>
                        <td className="px-5 py-3">
                          <Badge tone={isEntra ? 'info' : 'neutral'}>
                            {isEntra ? 'Microsoft' : 'Password'}
                          </Badge>
                        </td>
                        <td className="px-5 py-3">
                          <GroupCell
                            user={u}
                            groups={groups}
                            disabled={busyId === u.id || isLastAdmin}
                            hint={
                              isLastAdmin
                                ? 'The last admin must stay in an admin group — promote someone else first'
                                : undefined
                            }
                            onChange={(id) => assignGroup(u, id)}
                            onManageGroups={() => setShowGroups(true)}
                          />
                        </td>
                        <td className="px-5 py-3 text-right">
                          <div className="flex justify-end gap-2">
                            <Button
                              variant="ghost"
                              // An Entra account has no local password; setting one would create
                              // a way in that bypasses SSO, and the backend refuses it.
                              disabled={busyId === u.id || isEntra}
                              title={
                                isEntra
                                  ? 'This account signs in with Microsoft — its password is managed there'
                                  : undefined
                              }
                              onClick={() => setResetTarget(u)}
                            >
                              <KeyRound size={14} /> Reset password
                            </Button>
                            <Button
                              variant="ghost"
                              disabled={busyId === u.id || isSelf || isLastAdmin}
                              title={
                                isSelf
                                  ? "You can't delete your own account"
                                  : isLastAdmin
                                    ? 'Promote another admin first — the last admin cannot be deleted'
                                    : undefined
                              }
                              onClick={() => remove(u)}
                            >
                              <Trash2 size={14} /> Delete
                            </Button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>

      {showGroups && (
        <GroupsModal
          groups={groups}
          projects={projects}
          onClose={() => setShowGroups(false)}
          onChanged={refresh}
        />
      )}
      <CreateUserModal open={showCreate} groups={groups} onClose={() => setShowCreate(false)} onCreated={refresh} />
      <ResetPasswordModal
        user={resetTarget}
        onClose={() => setResetTarget(null)}
        onReset={refresh}
      />
    </div>
  )
}

/**
 * The group an account belongs to, and what that resolves to underneath.
 *
 * A plain select rather than a popover: there is exactly one choice to make, and "Guest" is a real
 * option in the list rather than a hidden state — removing someone's access should be as visible
 * as granting it.
 */
function GroupCell({
  user,
  groups,
  disabled,
  hint,
  onChange,
  onManageGroups,
}: {
  user: UserOut
  groups: Group[]
  disabled: boolean
  hint?: string
  onChange: (groupId: string | null) => void
  onManageGroups: () => void
}) {
  if (groups.length === 0)
    return (
      <button onClick={onManageGroups} className="text-xs font-semibold text-accent hover:opacity-80">
        Create the first group
      </button>
    )

  return (
    <div className="space-y-1">
      <select
        aria-label={`Group for ${user.username}`}
        value={user.group_id ?? ''}
        disabled={disabled}
        title={hint}
        onChange={(e) => onChange(e.target.value || null)}
        className="rounded-lg border border-hair bg-plane px-2 py-1 text-xs font-semibold text-ink outline-none focus:border-accent disabled:opacity-50"
      >
        <option value="">Guest — no access</option>
        {groups.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name} ({g.role})
          </option>
        ))}
      </select>
      <div className="text-xs text-muted">
        {user.role === 'admin'
          ? 'Sees every project, manages users and integrations'
          : user.role === 'guest'
            ? 'Sees nothing until assigned'
            : user.projects.length === 0
              ? 'Group grants no projects yet'
              : `Sees ${user.projects.join(', ')}`}
      </div>
    </div>
  )
}
