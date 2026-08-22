import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { timeAgo } from '../../lib/format'
import type { CloudConnection, PollResult, TestConnectionResult } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'

function pollResultText(result: PollResult): string {
  if (result.errors > 0) return 'Refresh failed — see status above'
  if (result.alarm_count === 0) return 'Refreshed: no active alarms'
  return `Refreshed: ${result.alarm_count} alarm(s) active`
}

export function CloudConnectionTable({
  rows,
  onDeleted,
  onEdit,
  onRefreshed,
}: {
  rows: CloudConnection[]
  onDeleted: (id: string) => void
  onEdit: (connection: CloudConnection) => void
  onRefreshed: (connection: CloudConnection) => void
}) {
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResult>>({})
  const [pollResults, setPollResults] = useState<Record<string, { ok: boolean; text: string }>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const test = async (id: string) => {
    setBusy(id)
    try {
      const result = await api.post<TestConnectionResult>(`/api/cloud-connections/${id}/test`, {})
      setTestResults((prev) => ({ ...prev, [id]: result }))
    } catch (e) {
      setTestResults((prev) => ({ ...prev, [id]: { ok: false, error: errText(e) } }))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: string) => {
    setBusy(id)
    try {
      await api.del(`/api/cloud-connections/${id}`)
      onDeleted(id)
    } finally {
      setBusy(null)
    }
  }

  const refresh = async (connection: CloudConnection) => {
    setBusy(connection.id)
    try {
      const result = await api.post<PollResult>(`/api/cloud-connections/${connection.id}/poll`, {})
      const updated = await api.get<CloudConnection[]>('/api/cloud-connections')
      const fresh = updated.find((c) => c.id === connection.id)
      if (fresh) onRefreshed(fresh)
      setPollResults((prev) => ({
        ...prev,
        [connection.id]: { ok: result.errors === 0, text: pollResultText(result) },
      }))
    } catch (e) {
      setPollResults((prev) => ({ ...prev, [connection.id]: { ok: false, text: errText(e) } }))
    } finally {
      setBusy(null)
    }
  }

  if (rows.length === 0) {
    return <p className="text-sm text-muted">No cloud connections yet.</p>
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-left text-muted">
        <tr>
          <th className="py-2">Project / Env</th>
          <th>Auth</th>
          <th>Region</th>
          <th>Last poll</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const result = testResults[c.id]
          const pollResult = pollResults[c.id]
          return (
            <tr key={c.id} className="border-t border-hair">
              <td className="py-2 text-ink">
                {c.project} / {c.env}
              </td>
              <td className="text-ink-2">{c.auth_type === 'sso' ? c.sso_profile_name : 'access key'}</td>
              <td className="text-ink-2">{c.region}</td>
              <td>
                {c.last_poll_status ? (
                  <div className="flex items-center gap-2">
                    <Badge tone={c.last_poll_status === 'ok' ? 'success' : 'danger'}>
                      {c.last_poll_status}
                    </Badge>
                    <span className="text-xs text-muted">
                      {c.last_poll_at && timeAgo(c.last_poll_at)}
                      {c.last_poll_alarm_count !== null && ` · ${c.last_poll_alarm_count} alarm(s)`}
                    </span>
                  </div>
                ) : (
                  <Badge tone="info">never polled</Badge>
                )}
              </td>
              <td className="py-2">
                <div className="flex items-center gap-2">
                  <Button variant="ghost" disabled={busy === c.id} onClick={() => refresh(c)}>
                    Refresh
                  </Button>
                  <Button variant="ghost" disabled={busy === c.id} onClick={() => test(c.id)}>
                    Test
                  </Button>
                  <Button variant="ghost" disabled={busy === c.id} onClick={() => onEdit(c)}>
                    Edit
                  </Button>
                  <Button variant="ghost" disabled={busy === c.id} onClick={() => remove(c.id)}>
                    Delete
                  </Button>
                  {result && (
                    <span className={result.ok ? 'text-sev-low' : 'text-sev-critical'}>
                      {result.ok ? 'OK' : result.error}
                    </span>
                  )}
                </div>
                {pollResult && (
                  <p className={pollResult.ok ? 'mt-1 text-xs text-sev-low' : 'mt-1 text-xs text-sev-critical'}>
                    {pollResult.text}
                  </p>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
