import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { Project } from '../../lib/types'
import { Button } from '../../components/ui/Button'

export function ProjectRegistry({
  projects,
  onCreated,
  onDeleted,
}: {
  projects: Project[]
  onCreated: (p: Project) => void
  onDeleted: (id: string) => void
}) {
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy('new')
    try {
      const created = await api.post<Project>('/api/projects', { name })
      onCreated(created)
      setName('')
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: string) => {
    setError(null)
    setBusy(id)
    try {
      await api.del(`/api/projects/${id}`)
      onDeleted(id)
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-hair bg-surface p-4">
      <h3 className="text-sm font-semibold text-muted">Projects</h3>
      <form onSubmit={add} className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. EVP"
          className="flex-1 rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent"
          required
        />
        <Button type="submit" disabled={busy === 'new'}>
          {busy === 'new' ? 'Adding…' : 'Add'}
        </Button>
      </form>
      {error && <p className="text-sm text-sev-critical">{error}</p>}
      {projects.length === 0 ? (
        <p className="text-sm text-muted">No projects yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {projects.map((p) => (
            <li
              key={p.id}
              className="flex items-center gap-2 rounded-full border border-hair bg-plane px-3 py-1 text-sm text-ink"
            >
              {p.name}
              <button
                type="button"
                disabled={busy === p.id}
                onClick={() => remove(p.id)}
                className="text-muted hover:text-sev-critical"
                aria-label={`Delete ${p.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
