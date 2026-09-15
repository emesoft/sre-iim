import { useEffect, useState } from 'react'
import { api, errText, isUnreachable } from './api'
import type { DocumentSummary, IncidentRollup, IncidentSummary } from './types'

export interface DashboardData {
  incidents: IncidentSummary[]
  docs: DocumentSummary[]
  rollup: IncidentRollup | null
  loading: boolean
  error: string | null
  unreachable: boolean
}

/**
 * Shared read of what the dashboard summarises. Lifted to App so the top bar (alert count) and
 * the Overview cards agree on one fetch. Re-runs whenever `refreshKey` changes.
 *
 * `rollup` is the source of truth for every count the dashboard shows. `incidents` is still
 * fetched for the lists that render actual rows, but it is one *page* of incidents — deriving
 * totals from it silently under-counts past that page, and can't produce per-project figures at
 * all, so don't reintroduce counts computed from it here.
 */
export function useDashboard(refreshKey: number): DashboardData {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [docs, setDocs] = useState<DocumentSummary[]>([])
  const [rollup, setRollup] = useState<IncidentRollup | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    Promise.all([
      api.get<IncidentSummary[]>('/api/incidents'),
      api.get<DocumentSummary[]>('/api/documents'),
      api.get<IncidentRollup>('/api/incidents/rollup'),
    ])
      .then(([i, d, r]) => {
        if (!alive) return
        setIncidents(i)
        setDocs(d)
        setRollup(r)
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

  return { incidents, docs, rollup, loading, error, unreachable }
}
