import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { CloudConnection, CloudConnectionCreate } from '../../lib/types'
import { Button } from '../../components/ui/Button'
import { SelectOrOtherField } from './SelectOrOtherField'

const KNOWN_PROJECTS = ['BEC', 'EVP', 'GCM', 'SmartSuite', 'IIM']
const KNOWN_ENVS = ['dev', 'qa', 'staging', 'prod']
const KNOWN_REGIONS = ['us-east-1', 'us-east-2', 'us-west-2', 'ap-southeast-1']

const inputCls =
  'mt-1 w-full rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent'

export function CloudConnectionForm({
  editing,
  onCreated,
  onUpdated,
  onCancelEdit,
}: {
  /** When set, the form edits this connection (PATCH) instead of creating a new one (POST). */
  editing?: CloudConnection | null
  onCreated?: (c: CloudConnection) => void
  onUpdated?: (c: CloudConnection) => void
  onCancelEdit?: () => void
}) {
  const [project, setProject] = useState(KNOWN_PROJECTS[0])
  const [env, setEnv] = useState(KNOWN_ENVS[3]) // prod
  const [region, setRegion] = useState(KNOWN_REGIONS[3]) // ap-southeast-1
  const [authType, setAuthType] = useState<'sso' | 'access_key'>('sso')
  const [ssoProfileName, setSsoProfileName] = useState('')
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!editing) return
    setProject(editing.project)
    setEnv(editing.env)
    setRegion(editing.region)
    setAuthType(editing.auth_type)
    setSsoProfileName(editing.sso_profile_name ?? '')
    setAccessKeyId('')
    setSecretAccessKey('')
    setError(null)
  }, [editing])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    const body: CloudConnectionCreate = {
      project,
      env,
      region,
      auth_type: authType,
      ...(authType === 'sso'
        ? { sso_profile_name: ssoProfileName }
        : { access_key_id: accessKeyId, secret_access_key: secretAccessKey }),
    }
    try {
      if (editing) {
        const updated = await api.patch<CloudConnection>(`/api/cloud-connections/${editing.id}`, body)
        onUpdated?.(updated)
      } else {
        const created = await api.post<CloudConnection>('/api/cloud-connections', body)
        onCreated?.(created)
      }
      setSsoProfileName('')
      setAccessKeyId('')
      setSecretAccessKey('')
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
          <h3 className="text-sm font-semibold text-muted">
            Editing {editing.project} / {editing.env}
          </h3>
          <Button type="button" variant="ghost" onClick={onCancelEdit}>
            Cancel
          </Button>
        </div>
      )}
      <div className="flex gap-3">
        <SelectOrOtherField label="Project" options={KNOWN_PROJECTS} value={project} onChange={setProject} />
        <SelectOrOtherField label="Env" options={KNOWN_ENVS} value={env} onChange={setEnv} />
        <SelectOrOtherField label="Region" options={KNOWN_REGIONS} value={region} onChange={setRegion} />
      </div>

      <div className="flex gap-4 text-sm text-ink-2">
        <label className="flex items-center gap-2">
          <input type="radio" checked={authType === 'sso'} onChange={() => setAuthType('sso')} />
          SSO profile
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            checked={authType === 'access_key'}
            onChange={() => setAuthType('access_key')}
          />
          Access key
        </label>
      </div>

      {authType === 'sso' ? (
        <label className="text-sm text-ink-2">
          SSO profile name (from the host's ~/.aws/config)
          <input
            value={ssoProfileName}
            onChange={(e) => setSsoProfileName(e.target.value)}
            placeholder="GCM-Prod-ReadOnlyAccess"
            className={inputCls}
            required
          />
        </label>
      ) : (
        <div className="flex gap-3">
          <label className="flex-1 text-sm text-ink-2">
            Access key ID
            <input
              value={accessKeyId}
              onChange={(e) => setAccessKeyId(e.target.value)}
              placeholder={editing ? 'Leave blank to keep the current key' : ''}
              className={inputCls}
              required={!editing}
            />
          </label>
          <label className="flex-1 text-sm text-ink-2">
            Secret access key
            <input
              type="password"
              value={secretAccessKey}
              onChange={(e) => setSecretAccessKey(e.target.value)}
              placeholder={editing ? 'Leave blank to keep the current secret' : ''}
              className={inputCls}
              required={!editing}
            />
          </label>
        </div>
      )}

      {error && <p className="text-sm text-sev-critical">{error}</p>}
      <Button type="submit" disabled={submitting} className="self-start">
        {submitting ? 'Saving…' : editing ? 'Save changes' : 'Add connection'}
      </Button>
    </form>
  )
}
