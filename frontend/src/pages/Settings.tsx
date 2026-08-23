import { useEffect, useState } from 'react'
import { api, errText } from '../lib/api'
import type { AdoConnection, CloudConnection, PollResult, PollSchedule, Project } from '../lib/types'
import { ProjectRegistry } from '../features/settings/ProjectRegistry'
import { ClaudeTokenForm } from '../features/settings/ClaudeTokenForm'
import { LlmUsageCard } from '../features/settings/LlmUsageCard'
import { AdminGate } from '../features/settings/AdminGate'
import { Button } from '../components/ui/Button'

function pollResultText(result: PollResult): string {
  if (result.errors > 0) {
    return `Refreshed ${result.polled} connection(s): ${result.errors} failed, ${result.alarm_count} alarm(s) active`
  }
  if (result.alarm_count === 0) return `Refreshed ${result.polled} connection(s): no active alarms`
  return `Refreshed ${result.polled} connection(s): ${result.alarm_count} alarm(s) active`
}

export function Settings() {
  return (
    <AdminGate>
      <SettingsContent />
    </AdminGate>
  )
}

function SettingsContent() {
  const [connections, setConnections] = useState<CloudConnection[]>([])
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [schedule, setSchedule] = useState<PollSchedule | null>(null)
  const [adoConnections, setAdoConnections] = useState<AdoConnection[]>([])
  const [projects, setProjects] = useState<Project[]>([])

  const load = async () => {
    try {
      setConnections(await api.get<CloudConnection[]>('/api/cloud-connections'))
    } catch {
      setConnections([]) // non-critical — each project's card just shows no AWS connections yet
    }
  }

  const loadSchedule = async () => {
    try {
      setSchedule(await api.get<PollSchedule>('/api/cloud-connections/poll-schedule'))
    } catch {
      setSchedule(null) // non-critical — the refresh-all flow works without it
    }
  }

  const loadAdo = async () => {
    try {
      setAdoConnections(await api.get<AdoConnection[]>('/api/ado-connections'))
    } catch {
      setAdoConnections([]) // non-critical — the ticket flow just reports "not configured"
    }
  }

  const loadProjects = async () => {
    try {
      setProjects(await api.get<Project[]>('/api/projects'))
    } catch {
      setProjects([]) // non-critical — the registry just shows "No projects yet" until this loads
    }
  }

  useEffect(() => {
    load()
    loadSchedule()
    loadAdo()
    loadProjects()
  }, [])

  const refreshNow = async () => {
    setRefreshing(true)
    setRefreshResult(null)
    try {
      const result = await api.post<PollResult>('/api/cloud-connections/poll', {})
      await load()
      setRefreshResult({ ok: result.errors === 0, text: pollResultText(result) })
    } catch (e) {
      setRefreshResult({ ok: false, text: errText(e) })
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in flex flex-col gap-6">
        <ClaudeTokenForm />
        <LlmUsageCard />

        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-muted">AWS connection polling</h3>
          <Button variant="ghost" disabled={refreshing} onClick={refreshNow}>
            {refreshing ? 'Refreshing…' : 'Refresh all'}
          </Button>
        </div>
        {schedule && (
          <p className="-mt-4 text-xs text-muted">
            Auto-polls every {schedule.interval_minutes} min
            {schedule.next_run_at &&
              ` — next run at ${new Date(schedule.next_run_at).toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
              })}`}
          </p>
        )}
        {refreshResult && (
          <p className={`-mt-4 text-xs ${refreshResult.ok ? 'text-sev-low' : 'text-sev-critical'}`}>
            {refreshResult.text}
          </p>
        )}

        <ProjectRegistry
          projects={projects}
          adoConnections={adoConnections}
          cloudConnections={connections}
          onProjectCreated={(p) => setProjects((prev) => [...prev, p])}
          onProjectDeleted={(id) => setProjects((prev) => prev.filter((p) => p.id !== id))}
          onAdoCreated={(c) => setAdoConnections((prev) => [...prev, c])}
          onAdoUpdated={(c) =>
            setAdoConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
          }
          onAdoDeleted={(id) => setAdoConnections((prev) => prev.filter((c) => c.id !== id))}
          onCloudCreated={(c) => setConnections((prev) => [...prev, c])}
          onCloudUpdated={(c) =>
            setConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
          }
          onCloudDeleted={(id) => setConnections((prev) => prev.filter((c) => c.id !== id))}
          onCloudRefreshed={(c) =>
            setConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
          }
        />
      </div>
    </div>
  )
}
