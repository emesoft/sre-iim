import { useEffect, useState } from 'react'
import { api, errText } from '../lib/api'
import type { CloudConnection, PollResult } from '../lib/types'
import { CloudConnectionForm } from '../features/settings/CloudConnectionForm'
import { CloudConnectionTable } from '../features/settings/CloudConnectionTable'
import { ClaudeTokenForm } from '../features/settings/ClaudeTokenForm'
import { Button } from '../components/ui/Button'

export function Settings() {
  const [connections, setConnections] = useState<CloudConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

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

  useEffect(() => {
    load()
  }, [])

  const refreshNow = async () => {
    setRefreshing(true)
    try {
      await api.post<PollResult>('/api/cloud-connections/poll', {})
      await load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in flex flex-col gap-6">
        <ClaudeTokenForm />

        <CloudConnectionForm onCreated={(c) => setConnections((prev) => [...prev, c])} />

        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-muted">AWS connections</h3>
          <Button variant="ghost" disabled={refreshing} onClick={refreshNow}>
            {refreshing ? 'Refreshing…' : 'Refresh now'}
          </Button>
        </div>

        {loading && <p className="text-sm text-muted">Loading…</p>}
        {error && <p className="text-sm text-sev-critical">{error}</p>}
        {!loading && !error && (
          <CloudConnectionTable
            rows={connections}
            onDeleted={(id) => setConnections((prev) => prev.filter((c) => c.id !== id))}
          />
        )}
      </div>
    </div>
  )
}
