import { useEffect, useState } from 'react'
import { getAuthToken } from './api'
import type { IncidentDetail, StageEvent } from './types'

export interface IncidentStreamState {
  stages: StageEvent[]
  result: IncidentDetail | null
  error: string | null
}

const IDLE: IncidentStreamState = { stages: [], result: null, error: null }

/**
 * Subscribes to an incident's SSE progress stream (`GET /api/incidents/{id}/stream`) while
 * `incidentId` is set. The backend always closes the stream after exactly one terminal event
 * (`analyzed` or `failed`) — whether that's live progress or an immediate snapshot for an
 * already-finished incident — so closing here on either event is the normal, expected end.
 */
export function useIncidentStream(incidentId: string | null): IncidentStreamState {
  const [state, setState] = useState<IncidentStreamState>(IDLE)

  useEffect(() => {
    setState(IDLE)
    if (!incidentId) return

    // EventSource can't send an Authorization header, so the JWT rides along as a query param
    // instead (get_current_user in deps.py accepts either) — see that function's docstring.
    const token = getAuthToken()
    const url = `/api/incidents/${incidentId}/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`
    const source = new EventSource(url)

    source.addEventListener('stage', (e) => {
      const data = JSON.parse((e as MessageEvent).data) as StageEvent
      setState((s) => ({ ...s, stages: [...s.stages, data] }))
    })
    source.addEventListener('analyzed', (e) => {
      const data = JSON.parse((e as MessageEvent).data) as IncidentDetail
      setState((s) => ({ ...s, result: data }))
      source.close()
    })
    source.addEventListener('failed', (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { message: string }
      setState((s) => ({ ...s, error: data.message }))
      source.close()
    })
    source.onerror = () => {
      // A genuine terminal event already closes the connection itself, so an error here means
      // the connection dropped before one arrived — surface it once, don't let EventSource retry.
      setState((s) => (s.result || s.error ? s : { ...s, error: 'Lost connection to the server' }))
      source.close()
    }

    return () => source.close()
  }, [incidentId])

  return state
}
