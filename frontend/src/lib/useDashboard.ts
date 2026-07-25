import { useEffect, useState } from 'react'
import { api, errText, isUnreachable } from './api'
import type { DocumentSummary, IncidentSummary } from './types'

export interface DashboardData {
  incidents: IncidentSummary[]
  docs: DocumentSummary[]
  loading: boolean
  error: string | null
  unreachable: boolean
}

/**
 * Shared read of the two collections the dashboard summarises (incidents +
 * documents). Lifted to App so the top bar (alert count) and the Overview
 * cards agree on one fetch. Re-runs whenever `refreshKey` changes.
 */
export function useDashboard(refreshKey: number): DashboardData {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([])
  const [docs, setDocs] = useState<DocumentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    Promise.all([
      api.get<IncidentSummary[]>('/api/incidents'),
      api.get<DocumentSummary[]>('/api/documents'),
    ])
      .then(([i, d]) => {
        if (!alive) return
        setIncidents(i)
        setDocs(d)
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

  return { incidents, docs, loading, error, unreachable }
}
