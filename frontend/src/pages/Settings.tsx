import { useEffect, useState } from 'react'
import { api, errText } from '../lib/api'
import type { AdoConnection, CloudConnection, PollResult, PollSchedule } from '../lib/types'
import { CloudConnectionForm } from '../features/settings/CloudConnectionForm'
import { CloudConnectionTable } from '../features/settings/CloudConnectionTable'
import { AdoConnectionForm } from '../features/settings/AdoConnectionForm'
import { AdoConnectionTable } from '../features/settings/AdoConnectionTable'
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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<{ ok: boolean; text: string } | null>(null)
  const [editing, setEditing] = useState<CloudConnection | null>(null)
  const [schedule, setSchedule] = useState<PollSchedule | null>(null)
  const [adoConnections, setAdoConnections] = useState<AdoConnection[]>([])
  const [editingAdo, setEditingAdo] = useState<AdoConnection | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setConnections(await api.get<CloudConnection[]>('/api/cloud-connections'))
    } catch (e) {
      setError(errText(e))
    } finally {
      setLoading(false)
    }
  }

  const loadSchedule = async () => {
    try {
      setSchedule(await api.get<PollSchedule>('/api/cloud-connections/poll-schedule'))
    } catch {
      setSchedule(null) // non-critical — the table/refresh flow works without it
    }
  }

  const loadAdo = async () => {
    try {
      setAdoConnections(await api.get<AdoConnection[]>('/api/ado-connections'))
    } catch {
      setAdoConnections([]) // non-critical — the ticket flow just reports "not configured"
    }
  }

  useEffect(() => {
    load()
    loadSchedule()
    loadAdo()
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

        <CloudConnectionForm
          editing={editing}
          onCreated={(c) => setConnections((prev) => [...prev, c])}
          onUpdated={(c) => {
            setConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
            setEditing(null)
          }}
          onCancelEdit={() => setEditing(null)}
        />

        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-muted">AWS connections</h3>
            <Button variant="ghost" disabled={refreshing} onClick={refreshNow}>
              {refreshing ? 'Refreshing…' : 'Refresh all'}
            </Button>
          </div>
          {schedule && (
            <p className="text-xs text-muted">
              Auto-polls every {schedule.interval_minutes} min
              {schedule.next_run_at &&
                ` — next run at ${new Date(schedule.next_run_at).toLocaleTimeString(undefined, {
                  hour: '2-digit',
                  minute: '2-digit',
                })}`}
            </p>
          )}
          {refreshResult && (
            <p className={refreshResult.ok ? 'text-xs text-sev-low' : 'text-xs text-sev-critical'}>
              {refreshResult.text}
            </p>
          )}
        </div>

        {loading && <p className="text-sm text-muted">Loading…</p>}
        {error && <p className="text-sm text-sev-critical">{error}</p>}
        {!loading && !error && (
          <CloudConnectionTable
            rows={connections}
            onDeleted={(id) => setConnections((prev) => prev.filter((c) => c.id !== id))}
            onEdit={setEditing}
            onRefreshed={(c) =>
              setConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
            }
          />
        )}

        <AdoConnectionForm
          editing={editingAdo}
          onCreated={(c) => setAdoConnections((prev) => [...prev, c])}
          onUpdated={(c) => {
            setAdoConnections((prev) => prev.map((existing) => (existing.id === c.id ? c : existing)))
            setEditingAdo(null)
          }}
          onCancelEdit={() => setEditingAdo(null)}
        />

        <h3 className="text-sm font-semibold text-muted">Azure DevOps connections</h3>
        <AdoConnectionTable
          rows={adoConnections}
          onDeleted={(id) => setAdoConnections((prev) => prev.filter((c) => c.id !== id))}
          onEdit={setEditingAdo}
        />
      </div>
    </div>
  )
}
