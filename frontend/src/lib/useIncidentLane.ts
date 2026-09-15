import { useEffect, useRef, useState } from 'react'
import { api, errText } from './api'
import type { IncidentRollup, IncidentSummary } from './types'

/** The triage lanes, in the order work flows through them. Mirrors `domain/incidents/lanes.py`;
 * the counts come from the rollup so they stay right past any page cap. */
export const LANES = [
  { id: 'triage', label: 'Needs triage' },
  { id: 'working', label: 'In progress' },
  { id: 'ticketed', label: 'Ticketed' },
  { id: 'done', label: 'Resolved' },
] as const

export type LaneId = (typeof LANES)[number]['id']

/**
 * One lane's incidents *and* the tab counts, both narrowed by the same project filter.
 *
 * Two rules meet here. The rows come per lane rather than from the dashboard's page of everything:
 * filter that page client-side and a "Needs triage 40" tab shows whichever twelve happened to land
 * on it. And the counts are re-fetched with the same `service` filter as the rows — a tab reading
 * "In progress 9" above a single filtered row is the same lie in the other direction, and it was
 * live for a while before someone noticed.
 */
/** How often the queue re-checks itself. Shorter than the alarm poll interval, so an incident the
 *  poller opens shows up within one cycle of it existing rather than whenever someone reloads. */
const REFRESH_MS = 30_000

export function useIncidentLane(lane: LaneId, project: string | null, refreshKey: number) {
  const [rows, setRows] = useState<IncidentSummary[]>([])
  const rowsRef = useRef<IncidentSummary[]>([])
  const [rollup, setRollup] = useState<IncidentRollup | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const [tick, setTick] = useState(0)
  const scope = project ? `&service=${encodeURIComponent(project)}` : ''

  // The incidents this page is for arrive on a timer, not from anything the reader did: the poller
  // opens them every few minutes in the background. Without this the queue silently showed the
  // state of the world at page load, and a critical alert that fired two minutes ago was simply
  // absent — indistinguishable from nothing having happened.
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), REFRESH_MS)
    // Coming back to the tab is the other moment the list is most likely to be stale, and the one
    // where a reader is about to trust it.
    const onFocus = () => setTick((n) => n + 1)
    window.addEventListener('focus', onFocus)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('focus', onFocus)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api
      .get<IncidentRollup>(`/api/incidents/rollup?window_hours=24${scope}`)
      .then((r) => !cancelled && setRollup(r))
      .catch(() => !cancelled && setRollup(null))
    return () => {
      cancelled = true
    }
  }, [scope, refreshKey, tick])

  useEffect(() => {
    let cancelled = false
    // Only the first load of a lane shows skeletons; a background refresh must not blank the rows
    // out from under someone mid-read.
    setLoading((was) => was && rowsRef.current.length === 0)
    api
      .get<IncidentSummary[]>(`/api/incidents?lane=${lane}&limit=200${scope}`)
      .then((data) => {
        if (cancelled) return
        setRows(data)
        rowsRef.current = data
        setError(null)
        setUnreachable(false)
      })
      .catch((e) => {
        if (cancelled) return
        setError(errText(e))
        setUnreachable(e instanceof TypeError)
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [lane, scope, refreshKey, tick])

  return {
    rows,
    loading,
    error,
    unreachable,
    laneCounts: rollup?.lanes ?? {},
    projects: rollup?.all_projects ?? [],
  }
}
