import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { AdoConnection, TestConnectionResult } from '../../lib/types'
import { Button } from '../../components/ui/Button'

export function AdoConnectionTable({
  rows,
  onDeleted,
  onEdit,
}: {
  rows: AdoConnection[]
  onDeleted: (id: string) => void
  onEdit: (connection: AdoConnection) => void
}) {
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResult>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const test = async (id: string) => {
    setBusy(id)
    try {
      const result = await api.post<TestConnectionResult>(`/api/ado-connections/${id}/test`, {})
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
      await api.del(`/api/ado-connections/${id}`)
      onDeleted(id)
    } finally {
      setBusy(null)
    }
  }

  if (rows.length === 0) {
    return <p className="text-sm text-muted">No Azure DevOps connections yet.</p>
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-left text-muted">
        <tr>
          <th className="py-2">Project</th>
          <th>ADO org / project</th>
          <th>Work item type</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => {
          const result = testResults[c.id]
          return (
            <tr key={c.id} className="border-t border-hair">
              <td className="py-2 text-ink">{c.project}</td>
              <td className="text-ink-2">
                {c.org} / {c.ado_project}
              </td>
              <td className="text-ink-2">{c.work_item_type}</td>
              <td className="py-2">
                <div className="flex items-center gap-2">
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
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
