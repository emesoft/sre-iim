import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { AdoConnection, AdoConnectionCreate } from '../../lib/types'
import { Button } from '../../components/ui/Button'
import { SelectOrOtherField } from './SelectOrOtherField'

const KNOWN_PROJECTS = ['BEC', 'EVP', 'GCM', 'SmartSuite', 'IIM']

const inputCls =
  'mt-1 w-full rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent'

export function AdoConnectionForm({
  editing,
  onCreated,
  onUpdated,
  onCancelEdit,
}: {
  /** When set, the form edits this connection (PATCH) instead of creating a new one (POST). */
  editing?: AdoConnection | null
  onCreated?: (c: AdoConnection) => void
  onUpdated?: (c: AdoConnection) => void
  onCancelEdit?: () => void
}) {
  const [project, setProject] = useState(KNOWN_PROJECTS[0])
  const [org, setOrg] = useState('')
  const [adoProject, setAdoProject] = useState('')
  const [pat, setPat] = useState('')
  const [workItemType, setWorkItemType] = useState('Bug')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!editing) return
    setProject(editing.project)
    setOrg(editing.org)
    setAdoProject(editing.ado_project)
    setWorkItemType(editing.work_item_type)
    setPat('')
    setError(null)
  }, [editing])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    const body: AdoConnectionCreate = {
      project,
      org,
      ado_project: adoProject,
      work_item_type: workItemType,
      ...(pat ? { pat } : {}),
    }
    try {
      if (editing) {
        const updated = await api.patch<AdoConnection>(`/api/ado-connections/${editing.id}`, body)
        onUpdated?.(updated)
      } else {
        const created = await api.post<AdoConnection>('/api/ado-connections', body)
        onCreated?.(created)
      }
      setOrg('')
      setAdoProject('')
      setPat('')
      setWorkItemType('Bug')
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-2xl border border-hair bg-surface p-4">
      {editing && (
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-muted">Editing {editing.project}</h3>
          <Button type="button" variant="ghost" onClick={onCancelEdit}>
            Cancel
          </Button>
        </div>
      )}
      <div className="flex gap-3">
        <SelectOrOtherField label="Project" options={KNOWN_PROJECTS} value={project} onChange={setProject} />
        <label className="flex-1 text-sm text-ink-2">
          ADO organization
          <input
            value={org}
            onChange={(e) => setOrg(e.target.value)}
            placeholder="my-org"
            className={inputCls}
            required
          />
        </label>
        <label className="flex-1 text-sm text-ink-2">
          ADO project name
          <input
            value={adoProject}
            onChange={(e) => setAdoProject(e.target.value)}
            placeholder="EVP-Board"
            className={inputCls}
            required
          />
        </label>
      </div>

      <div className="flex gap-3">
        <label className="flex-1 text-sm text-ink-2">
          Personal Access Token
          <input
            type="password"
            value={pat}
            onChange={(e) => setPat(e.target.value)}
            placeholder={editing ? 'Leave blank to keep the current token' : ''}
            className={inputCls}
            required={!editing}
          />
        </label>
        <label className="flex-1 text-sm text-ink-2">
          Work item type
          <input
            value={workItemType}
            onChange={(e) => setWorkItemType(e.target.value)}
            placeholder="Bug"
            className={inputCls}
          />
        </label>
      </div>

      {error && <p className="text-sm text-sev-critical">{error}</p>}
      <Button type="submit" disabled={submitting} className="self-start">
        {submitting ? 'Saving…' : editing ? 'Save changes' : 'Add connection'}
      </Button>
    </form>
  )
}
