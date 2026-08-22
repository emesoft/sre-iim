import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { CloudConnection, CloudConnectionCreate } from '../../lib/types'
import { Button } from '../../components/ui/Button'

const KNOWN_PROJECTS = ['BEC', 'EVP', 'GCM', 'SmartSuite', 'IIM']

const inputCls =
  'mt-1 w-full rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent'

export function CloudConnectionForm({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [project, setProject] = useState(KNOWN_PROJECTS[0])
  const [env, setEnv] = useState('prod')
  const [region, setRegion] = useState('ap-southeast-1')
  const [authType, setAuthType] = useState<'sso' | 'access_key'>('sso')
  const [ssoProfileName, setSsoProfileName] = useState('')
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

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
      const created = await api.post<CloudConnection>('/api/cloud-connections', body)
      onCreated(created)
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
      <div className="flex gap-3">
        <label className="flex-1 text-sm text-ink-2">
          Project
          <select value={project} onChange={(e) => setProject(e.target.value)} className={inputCls}>
            {KNOWN_PROJECTS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex-1 text-sm text-ink-2">
          Env
          <input value={env} onChange={(e) => setEnv(e.target.value)} className={inputCls} />
        </label>
        <label className="flex-1 text-sm text-ink-2">
          Region
          <input value={region} onChange={(e) => setRegion(e.target.value)} className={inputCls} />
        </label>
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
              className={inputCls}
              required
            />
          </label>
          <label className="flex-1 text-sm text-ink-2">
            Secret access key
            <input
              type="password"
              value={secretAccessKey}
              onChange={(e) => setSecretAccessKey(e.target.value)}
              className={inputCls}
              required
            />
          </label>
        </div>
      )}

      {error && <p className="text-sm text-sev-critical">{error}</p>}
      <Button type="submit" disabled={submitting} className="self-start">
        {submitting ? 'Adding…' : 'Add connection'}
      </Button>
    </form>
  )
}
