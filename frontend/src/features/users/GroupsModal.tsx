import { useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { Group, GroupWrite, LlmSetup, Project, Role } from '../../lib/types'
import { ROLE_LABELS } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'

const ROLES: Role[] = ['admin', 'sre', 'consultant']

/**
 * Groups: what someone may do and which projects they see, as one thing.
 *
 * These were two separate controls — a role dropdown and a team picker — and no arrangement of
 * them explained which answered what. A group answers both at once, so "SRE · gcm" is a complete
 * description of an account.
 */
export function GroupsModal({
  groups,
  projects,
  onClose,
  onChanged,
}: {
  groups: Group[]
  projects: Project[]
  onClose: () => void
  onChanged: () => void
}) {
  const [editing, setEditing] = useState<Group | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const remove = async (group: Group) => {
    if (
      !confirm(
        `Delete "${group.name}"? Its ${group.member_count} member(s) become guests — no ` +
          `permissions and no projects until they're put in another group.`,
      )
    )
      return
    try {
      await api.del(`/api/groups/${group.id}`)
      onChanged()
    } catch (e) {
      setError(errText(e))
    }
  }

  if (editing)
    return (
      <GroupForm
        group={editing === 'new' ? null : editing}
        projects={projects.map((p) => p.name)}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null)
          onChanged()
        }}
      />
    )

  return (
    <Modal open onClose={onClose} title="Groups">
      <div className="space-y-3">
        <p className="text-sm text-ink-2">
          A group says what its members may do and which projects they see. Put people in one from
          the users table.
        </p>

        {error && <p className="text-sm text-sev-critical">{error}</p>}

        <div className="divide-y divide-hair rounded-xl border border-hair">
          {groups.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted">
              No groups yet — the first one is how anybody gets access.
            </p>
          ) : (
            groups.map((group) => (
              <div key={group.id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-ink">{group.name}</span>
                    <Badge tone={group.role === 'admin' ? 'warning' : 'neutral'}>
                      {group.role}
                    </Badge>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {group.role === 'admin' ? (
                      <span className="text-xs text-muted">every project</span>
                    ) : group.projects.length === 0 ? (
                      <span className="text-xs text-muted">no projects yet — grants nothing</span>
                    ) : (
                      group.projects.map((p) => (
                        <Badge key={p} tone="accent">
                          {p}
                        </Badge>
                      ))
                    )}
                  </div>
                </div>
                <span className="shrink-0 text-xs text-muted">
                  {group.member_count} {group.member_count === 1 ? 'person' : 'people'}
                </span>
                <Button variant="ghost" onClick={() => setEditing(group)}>
                  <Pencil size={14} />
                </Button>
                <Button variant="ghost" onClick={() => remove(group)}>
                  <Trash2 size={14} />
                </Button>
              </div>
            ))
          )}
        </div>

        <div className="flex justify-end">
          <Button onClick={() => setEditing('new')}>
            <Plus size={15} /> New group
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function GroupForm({
  group,
  projects,
  onClose,
  onSaved,
}: {
  group: Group | null
  projects: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(group?.name ?? '')
  const [role, setRole] = useState<Role>(group?.role ?? 'sre')
  const [profileId, setProfileId] = useState(group?.model_profile_id ?? '')
  const [llm, setLlm] = useState<LlmSetup | null>(null)
  const [selected, setSelected] = useState<string[]>(group?.projects ?? [])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.get<LlmSetup>('/api/settings/llm').then(setLlm).catch(() => setLlm(null))
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const body: GroupWrite = {
      name: name.trim(),
      role,
      description: group?.description ?? null,
      projects: selected,
      model_profile_id: profileId || null,
    }
    try {
      if (group) await api.patch<Group>(`/api/groups/${group.id}`, body)
      else await api.post<Group>('/api/groups', body)
      onSaved()
    } catch (err) {
      setError(errText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={group ? `Edit ${group.name}` : 'New group'}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-2">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="SRE · gcm"
            required
            autoFocus
            className="w-full rounded-lg border border-hair bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
        </label>

        <div className="space-y-1.5">
          <span className="text-xs font-medium text-ink-2">What members may do</span>
          {ROLES.map((r) => (
            <label
              key={r}
              className={`flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition ${
                role === r ? 'border-accent bg-accent-weak' : 'border-hair hover:border-ink-2'
              }`}
            >
              <input
                type="radio"
                name="role"
                checked={role === r}
                onChange={() => setRole(r)}
                className="mt-0.5 accent-[var(--accent)]"
              />
              <span>
                <span className="block text-sm font-semibold capitalize text-ink">{r}</span>
                <span className="block text-xs text-muted">{ROLE_LABELS[r]}</span>
              </span>
            </label>
          ))}
        </div>

        <div className="space-y-1">
          <span className="text-xs font-medium text-ink-2">Projects it opens up</span>
          {role === 'admin' ? (
            // Saying "every project" is the honest label: the backend doesn't filter admins, so a
            // project list here would describe a limit nothing enforces.
            <p className="text-xs text-muted">
              Admins see every project — there's nothing to pick.
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {projects.length === 0 ? (
                <span className="text-xs text-muted">No projects exist yet.</span>
              ) : (
                projects.map((p) => {
                  const on = selected.includes(p)
                  return (
                    <button
                      key={p}
                      type="button"
                      onClick={() =>
                        setSelected(on ? selected.filter((v) => v !== p) : [...selected, p])
                      }
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                        on
                          ? 'border-accent bg-accent-weak text-accent'
                          : 'border-hair bg-surface text-muted hover:text-ink-2'
                      }`}
                    >
                      {p}
                    </button>
                  )
                })
              )}
            </div>
          )}
        </div>

        {llm && llm.profiles.length > 0 && (
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-2">AI spend billed to</span>
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              className="w-full rounded-lg border border-hair bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            >
              <option value="">Deployment default</option>
              {llm.profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <span className="block text-xs leading-relaxed text-muted">
              Analyses this group&rsquo;s members run use its own key, so the usage table can tell
              their spend apart. A project pinned to its own profile still wins — that rule is about
              where data may go, not who pays.
            </span>
          </label>
        )}

        {error && <p className="text-sm text-sev-critical">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : group ? 'Save' : 'Create group'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
