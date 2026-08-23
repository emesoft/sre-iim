import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { AdoConnection, Project, TestConnectionResult } from '../../lib/types'
import { Button } from '../../components/ui/Button'

const inputCls =
  'mt-1 w-full rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent'

/**
 * Projects + their optional Azure DevOps ticket destination in one place — a project's ADO org/
 * PAT/work-item-type are provided (or left blank) right where the project itself is managed,
 * instead of a separate "Azure DevOps connections" section the user has to cross-reference by name.
 */
export function ProjectRegistry({
  projects,
  adoConnections,
  onProjectCreated,
  onProjectDeleted,
  onAdoCreated,
  onAdoUpdated,
  onAdoDeleted,
}: {
  projects: Project[]
  adoConnections: AdoConnection[]
  onProjectCreated: (p: Project) => void
  onProjectDeleted: (id: string) => void
  onAdoCreated: (c: AdoConnection) => void
  onAdoUpdated: (c: AdoConnection) => void
  onAdoDeleted: (id: string) => void
}) {
  const [name, setName] = useState('')
  const [org, setOrg] = useState('')
  const [adoProject, setAdoProject] = useState('')
  const [pat, setPat] = useState('')
  const [workItemType, setWorkItemType] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestConnectionResult>>({})

  // Set when editing an existing project's ADO connection, or adding one to a project that
  // doesn't have one yet — either way the "Project" field locks to that project's name.
  const [lockedProject, setLockedProject] = useState<string | null>(null)
  const [editingAdo, setEditingAdo] = useState<AdoConnection | null>(null)

  const resetForm = () => {
    setName('')
    setOrg('')
    setAdoProject('')
    setPat('')
    setWorkItemType('')
    setLockedProject(null)
    setEditingAdo(null)
  }

  const startAddAdo = (projectName: string) => {
    setError(null)
    setLockedProject(projectName)
    setEditingAdo(null)
    setOrg('')
    setAdoProject('')
    setPat('')
    setWorkItemType('')
  }

  const startEditAdo = (connection: AdoConnection) => {
    setError(null)
    setLockedProject(connection.project)
    setEditingAdo(connection)
    setOrg(connection.org)
    setAdoProject(connection.ado_project)
    setPat('')
    setWorkItemType(connection.work_item_type)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy('form')
    try {
      if (editingAdo) {
        const updated = await api.patch<AdoConnection>(`/api/ado-connections/${editingAdo.id}`, {
          project: editingAdo.project,
          org,
          ado_project: adoProject,
          work_item_type: workItemType || 'Bug',
          ...(pat ? { pat } : {}),
        })
        onAdoUpdated(updated)
        resetForm()
      } else if (lockedProject) {
        const created = await api.post<AdoConnection>('/api/ado-connections', {
          project: lockedProject,
          org,
          ado_project: adoProject,
          pat,
          work_item_type: workItemType || 'Bug',
        })
        onAdoCreated(created)
        resetForm()
      } else {
        const project = await api.post<Project>('/api/projects', { name })
        onProjectCreated(project)
        if (org && adoProject && pat) {
          try {
            const adoConn = await api.post<AdoConnection>('/api/ado-connections', {
              project: project.name,
              org,
              ado_project: adoProject,
              pat,
              work_item_type: workItemType || 'Bug',
            })
            onAdoCreated(adoConn)
          } catch (adoErr) {
            setError(`Project created, but the Azure DevOps connection failed: ${errText(adoErr)}`)
          }
        }
        resetForm()
      }
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const removeProject = async (id: string) => {
    setError(null)
    setBusy(id)
    try {
      await api.del(`/api/projects/${id}`)
      onProjectDeleted(id)
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const removeAdo = async (connection: AdoConnection) => {
    setError(null)
    setBusy(connection.id)
    try {
      await api.del(`/api/ado-connections/${connection.id}`)
      onAdoDeleted(connection.id)
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const testAdo = async (connection: AdoConnection) => {
    setBusy(connection.id)
    try {
      const result = await api.post<TestConnectionResult>(
        `/api/ado-connections/${connection.id}/test`,
        {}
      )
      setTestResults((prev) => ({ ...prev, [connection.id]: result }))
    } catch (err) {
      setTestResults((prev) => ({ ...prev, [connection.id]: { ok: false, error: errText(err) } }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-hair bg-surface p-4">
      <h3 className="text-sm font-semibold text-muted">Projects</h3>

      <form onSubmit={submit} className="flex flex-col gap-3">
        {lockedProject ? (
          <div className="flex items-center justify-between">
            <p className="text-sm text-ink-2">
              {editingAdo ? 'Editing' : 'Adding'} Azure DevOps for <strong>{lockedProject}</strong>
            </p>
            <Button type="button" variant="ghost" onClick={resetForm}>
              Cancel
            </Button>
          </div>
        ) : (
          <label className="text-sm text-ink-2">
            Project
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. EVP"
              className={inputCls}
              required
            />
          </label>
        )}

        <p className="text-xs text-muted">
          Azure DevOps ticket destination (optional) — fill these in to file tickets for this
          project's incidents, or leave blank and add it later.
        </p>
        <div className="flex gap-3">
          <label className="flex-1 text-sm text-ink-2">
            ADO organization
            <input
              value={org}
              onChange={(e) => setOrg(e.target.value)}
              placeholder="my-org"
              className={inputCls}
            />
          </label>
          <label className="flex-1 text-sm text-ink-2">
            ADO project name
            <input
              value={adoProject}
              onChange={(e) => setAdoProject(e.target.value)}
              placeholder="EVP-Board"
              className={inputCls}
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
              placeholder={editingAdo ? 'Leave blank to keep the current token' : ''}
              className={inputCls}
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
        <Button type="submit" disabled={busy === 'form'} className="self-start">
          {busy === 'form'
            ? 'Saving…'
            : editingAdo
              ? 'Save changes'
              : lockedProject
                ? 'Add Azure DevOps connection'
                : 'Add project'}
        </Button>
      </form>

      {projects.length === 0 ? (
        <p className="text-sm text-muted">No projects yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-2">Project</th>
              <th>Azure DevOps</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => {
              const ado = adoConnections.find((c) => c.project === p.name)
              const result = ado ? testResults[ado.id] : undefined
              return (
                <tr key={p.id} className="border-t border-hair">
                  <td className="py-2 text-ink">{p.name}</td>
                  <td className="text-ink-2">
                    {ado ? (
                      <>
                        {ado.org} / {ado.ado_project}
                        {result && (
                          <span className={result.ok ? 'ml-2 text-sev-low' : 'ml-2 text-sev-critical'}>
                            {result.ok ? 'OK' : result.error}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-muted">not configured</span>
                    )}
                  </td>
                  <td className="py-2">
                    <div className="flex items-center gap-2">
                      {ado ? (
                        <>
                          <Button variant="ghost" disabled={busy === ado.id} onClick={() => testAdo(ado)}>
                            Test
                          </Button>
                          <Button variant="ghost" disabled={busy === ado.id} onClick={() => startEditAdo(ado)}>
                            Edit ADO
                          </Button>
                          <Button variant="ghost" disabled={busy === ado.id} onClick={() => removeAdo(ado)}>
                            Remove ADO
                          </Button>
                        </>
                      ) : (
                        <Button variant="ghost" onClick={() => startAddAdo(p.name)}>
                          + Add ADO
                        </Button>
                      )}
                      <Button variant="ghost" disabled={busy === p.id} onClick={() => removeProject(p.id)}>
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
