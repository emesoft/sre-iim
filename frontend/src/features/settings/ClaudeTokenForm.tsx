import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { SettingStatus } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'

/**
 * Local-demo only: authenticates the `claude_cli` LLM provider with a Claude Code subscription
 * token (`claude setup-token`) instead of an Anthropic API key. See
 * backend/app/infrastructure/llm/claude_cli.py for why — not for production use.
 */
export function ClaudeTokenForm() {
  const [status, setStatus] = useState<SettingStatus | null>(null)
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try {
      setStatus(await api.get<SettingStatus>('/api/settings/claude-token'))
    } catch (e) {
      setError(errText(e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSaving(true)
    try {
      await api.put('/api/settings/claude-token', { token })
      setToken('')
      await load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-2xl border border-hair p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted">Claude Code token (local demo only)</h3>
        {status && (
          <Badge tone={status.is_set ? 'success' : 'neutral'}>
            {status.is_set ? 'Configured' : 'Not configured'}
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted">
        Run <code>claude setup-token</code> on this machine, paste the token below. Uses your Claude
        Code subscription — not billed per API token, and not for production deployments.
      </p>
      <div className="flex gap-3">
        <input
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="sk-ant-oat-..."
          className="flex-1 rounded-lg border border-hair bg-surface p-2"
          required
        />
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
      {error && <p className="text-sm text-sev-critical">{error}</p>}
    </form>
  )
}
