import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { SettingStatus, TestConnectionResult } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'

const SETUP_TOKEN_COMMAND = 'claude setup-token'

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
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)
  const [copied, setCopied] = useState(false)

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
      setTestResult(null)
      await load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setSaving(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.post<TestConnectionResult>('/api/settings/claude-token/test', {}))
    } catch (e) {
      setTestResult({ ok: false, error: errText(e) })
    } finally {
      setTesting(false)
    }
  }

  const copyCommand = async () => {
    try {
      await navigator.clipboard.writeText(SETUP_TOKEN_COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard blocked (e.g. insecure context) — the command is still visible to copy by hand
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
        {status?.is_set && (
          <Button type="button" variant="ghost" disabled={testing} onClick={runTest}>
            {testing ? 'Testing…' : 'Test'}
          </Button>
        )}
      </div>

      {testResult?.ok === true && <p className="text-sm text-sev-low">Token works.</p>}

      {testResult?.ok === false && (
        <div className="flex flex-col gap-2 rounded-lg border border-hair bg-plane p-3">
          <p className="text-sm text-sev-critical">
            Token isn't working{testResult.error ? `: ${testResult.error}` : ''}. Get a new one:
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 rounded bg-surface p-2 text-xs">{SETUP_TOKEN_COMMAND}</code>
            <Button type="button" variant="ghost" onClick={copyCommand}>
              {copied ? 'Copied!' : 'Copy'}
            </Button>
          </div>
          <p className="text-xs text-muted">
            Run this on the machine running <code>docker compose</code> — it opens a browser to sign
            in, then prints a new token. Paste it above and Save.
          </p>
        </div>
      )}

      {error && <p className="text-sm text-sev-critical">{error}</p>}
    </form>
  )
}
