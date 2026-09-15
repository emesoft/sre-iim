import { useEffect, useMemo, useState } from 'react'
import { Boxes, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { Integration, PollResult, PollSchedule, Project, Provider } from '../../lib/types'
import { Button } from '../../components/ui/Button'
import { Badge } from '../../components/ui/Badge'
import { EmptyState } from '../../components/ui/EmptyState'
import { IntegrationCard } from './IntegrationCard'
import { IntegrationForm } from './IntegrationForm'

/**
 * Settings' main panel: projects on the left, that project's integrations on the right.
 *
 * The list used to be every connection of every project on one scrolling page, which stops being
 * readable at a handful of projects — and a project is how people actually think about this ("what
 * is gcm wired up to?"), not "show me all the AWS accounts". Picking a project here is also what
 * the dashboard's project board links into.
 */
export function IntegrationsPanel({
  projects,
  onProjectsChanged,
}: {
  projects: Project[]
  onProjectsChanged: () => void
}) {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState<Integration | null>(null)
  const [adding, setAdding] = useState(false)
  const [newProject, setNewProject] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [schedule, setSchedule] = useState<PollSchedule | null>(null)
  const [pollResult, setPollResult] = useState<{ ok: boolean; text: string } | null>(null)

  const load = async () => {
    try {
      setIntegrations(await api.get<Integration[]>('/api/integrations'))
    } catch (e) {
      setError(errText(e))
    }
  }

  useEffect(() => {
    load()
    api.get<Provider[]>('/api/providers').then(setProviders).catch(() => setProviders([]))
    api
      .get<PollSchedule>('/api/integrations/poll-schedule')
      .then(setSchedule)
      // Non-critical: the panel works without knowing when the next sweep is.
      .catch(() => setSchedule(null))
  }, [])

  // Default to the first project so the right pane is never pointlessly empty on arrival.
  const active = selected ?? projects[0]?.name ?? null
  useEffect(() => {
    if (selected && !projects.some((p) => p.name === selected)) setSelected(null)
  }, [projects, selected])

  const byProject = useMemo(() => {
    const map = new Map<string, Integration[]>()
    for (const i of integrations) map.set(i.project, [...(map.get(i.project) ?? []), i])
    return map
  }, [integrations])

  const createProject = async () => {
    const name = newProject.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      await api.post<Project>('/api/projects', { name })
      setNewProject('')
      setSelected(name)
      onProjectsChanged()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const deleteProject = async (project: Project) => {
    setBusy(true)
    setError(null)
    try {
      await api.del(`/api/projects/${project.id}`)
      onProjectsChanged()
    } catch (e) {
      // The backend refuses while integrations still reference it — say so plainly.
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const toggleAutoAnalyze = async (project: Project) => {
    setBusy(true)
    try {
      await api.post<Project>(
        `/api/projects/${project.id}/auto-analyze?enabled=${!project.auto_analyze}`,
        {},
      )
      onProjectsChanged()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const refreshAll = async () => {
    setBusy(true)
    setPollResult(null)
    try {
      const r = await api.post<PollResult>('/api/integrations/poll', {})
      await load()
      setPollResult({
        ok: r.errors === 0,
        text:
          r.errors > 0
            ? `Polled ${r.polled}: ${r.errors} failed, ${r.alarm_count} alarm(s) active`
            : `Polled ${r.polled}: ${r.alarm_count} alarm(s) active`,
      })
    } catch (e) {
      setPollResult({ ok: false, text: errText(e) })
    } finally {
      setBusy(false)
    }
  }

  const activeProject = projects.find((p) => p.name === active) ?? null
  const rows = active ? (byProject.get(active) ?? []) : []

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr]">
      {/* Master: projects */}
      <aside className="flex flex-col gap-2">
        <div className="px-1 text-[10px] font-bold uppercase tracking-[0.16em] text-muted">
          Projects
        </div>
        {projects.map((p) => {
          const count = byProject.get(p.name)?.length ?? 0
          const failing = (byProject.get(p.name) ?? []).some((i) =>
            i.health.some((h) => h.status === 'error'),
          )
          return (
            <button
              key={p.id}
              onClick={() => setSelected(p.name)}
              className={`flex items-center gap-2 rounded-xl border px-3 py-2.5 text-left text-sm transition ${
                p.name === active
                  ? 'border-accent bg-accent-weak font-semibold text-ink'
                  : 'border-hair bg-surface text-ink-2 hover:bg-surface-2'
              }`}
            >
              <span className="min-w-0 flex-1 truncate">{p.name}</span>
              {failing && (
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: 'var(--sev-critical)' }}
                  title="An integration in this project is failing"
                />
              )}
              <span className="shrink-0 text-xs text-muted">{count}</span>
            </button>
          )
        })}

        <div className="mt-1 flex gap-1.5">
          <input
            value={newProject}
            onChange={(e) => setNewProject(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && createProject()}
            placeholder="New project"
            className="min-w-0 flex-1 rounded-lg border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-accent"
          />
          <Button variant="ghost" disabled={busy || !newProject.trim()} onClick={createProject}>
            <Plus size={14} />
          </Button>
        </div>
      </aside>

      {/* Detail: the selected project's integrations */}
      <section className="min-w-0">
        {error && <p className="mb-3 text-sm text-sev-critical">{error}</p>}

        {activeProject ? (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <h3 className="font-display text-lg font-bold text-ink">{activeProject.name}</h3>
                <Badge tone={activeProject.auto_analyze ? 'success' : 'neutral'}>
                  {activeProject.auto_analyze ? 'auto-triage on' : 'auto-triage paused'}
                </Badge>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="ghost" disabled={busy} onClick={() => toggleAutoAnalyze(activeProject)}>
                  <Sparkles size={14} />
                  {activeProject.auto_analyze ? 'Pause auto-triage' : 'Resume auto-triage'}
                </Button>
                <Button variant="ghost" disabled={busy} onClick={refreshAll}>
                  <RefreshCw size={14} /> Refresh all
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => deleteProject(activeProject)}>
                  <Trash2 size={14} /> Delete project
                </Button>
                <Button onClick={() => setAdding(true)}>
                  <Plus size={15} /> Add integration
                </Button>
              </div>
            </div>

            <p className="mb-4 text-xs text-muted">
              {schedule
                ? `Alarms are polled every ${schedule.interval_minutes} min` +
                  (schedule.next_run_at
                    ? ` — next run at ${new Date(schedule.next_run_at).toLocaleTimeString(undefined, {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}`
                    : '')
                : 'Alarms are polled on a schedule.'}
              {pollResult && (
                <span className={pollResult.ok ? ' text-sev-low' : ' text-sev-critical'}>
                  {' · '}
                  {pollResult.text}
                </span>
              )}
            </p>

            {rows.length === 0 ? (
              <EmptyState
                icon={Boxes}
                title="No integrations yet"
                hint={`Connect AWS, New Relic or Azure DevOps to ${activeProject.name}.`}
                action={
                  <Button onClick={() => setAdding(true)}>
                    <Plus size={15} /> Add integration
                  </Button>
                }
              />
            ) : (
              <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                {rows.map((integration) => (
                  <IntegrationCard
                    key={integration.id}
                    integration={integration}
                    provider={providers.find((p) => p.provider === integration.provider) ?? null}
                    onChanged={load}
                    onEdit={() => setEditing(integration)}
                  />
                ))}
              </div>
            )}
          </>
        ) : (
          <EmptyState
            icon={Boxes}
            title="No projects yet"
            hint="Create one on the left, then connect the systems it runs on."
          />
        )}
      </section>

      {(adding || editing) && (
        <IntegrationForm
          providers={providers}
          projects={projects.map((p) => p.name)}
          project={active ?? ''}
          existing={editing}
          onClose={() => {
            setAdding(false)
            setEditing(null)
          }}
          onSaved={async () => {
            setAdding(false)
            setEditing(null)
            await load()
          }}
        />
      )}
    </div>
  )
}
