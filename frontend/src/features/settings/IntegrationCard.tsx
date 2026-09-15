import { useState } from 'react'
import { CheckCircle2, PauseCircle, Pencil, PlayCircle, RefreshCw, Trash2, XCircle } from 'lucide-react'
import { api, errText } from '../../lib/api'
import { timeAgo } from '../../lib/format'
import type { Integration, PollResult, Provider, TestConnectionResult } from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'

/** Provider glyph: two letters is enough to tell them apart at a glance and needs no icon set. */
function ProviderMark({ provider, label }: { provider: string; label: string }) {
  const tone: Record<string, string> = {
    aws: 'var(--sev-medium)',
    newrelic: 'var(--info)',
    azure_devops: 'var(--purple)',
  }
  const color = tone[provider] ?? 'var(--accent)'
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-[11px] font-bold uppercase"
      style={{ background: `color-mix(in srgb, ${color} 15%, transparent)`, color }}
    >
      {label.replace(/[^A-Za-z]/g, '').slice(0, 2)}
    </span>
  )
}

/**
 * One integration. Capabilities are shown as chips with their own health, because that's the unit
 * that actually succeeds or fails: an AWS credential can be polling alarms happily while its cost
 * sync is broken, and one green tick over the whole card would hide that.
 */
export function IntegrationCard({
  integration,
  provider,
  onChanged,
  onEdit,
}: {
  integration: Integration
  provider: Provider | null
  onChanged: () => Promise<void> | void
  onEdit: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const act = async (label: string, fn: () => Promise<string | null>) => {
    setBusy(label)
    setResult(null)
    try {
      const text = await fn()
      if (text) setResult({ ok: true, text })
      await onChanged()
    } catch (e) {
      setResult({ ok: false, text: errText(e) })
    } finally {
      setBusy(null)
    }
  }

  const test = () =>
    act('test', async () => {
      const r = await api.post<TestConnectionResult>(
        `/api/integrations/${integration.id}/test`,
        {},
      )
      if (!r.ok) throw new Error(r.error ?? 'Connection test failed')
      return 'Connection OK'
    })

  const refresh = () =>
    act('refresh', async () => {
      const r = await api.post<PollResult>(`/api/integrations/${integration.id}/poll`, {})
      if (r.errors > 0) throw new Error('Poll failed — see the alarms chip below')
      return `Polled: ${r.alarm_count} alarm(s) active`
    })

  const togglePause = () =>
    act('pause', async () => {
      const action = integration.enabled ? 'pause' : 'resume'
      await api.post<Integration>(`/api/integrations/${integration.id}/${action}`, {})
      return null
    })

  const remove = () =>
    act('delete', async () => {
      await api.del(`/api/integrations/${integration.id}`)
      return null
    })

  const canPoll = integration.capabilities.includes('alarms')
  const label = provider?.label ?? integration.provider
  // What the provider offers but this integration doesn't have switched on.
  const inactive = (provider?.capabilities ?? []).filter(
    (c) => !integration.capabilities.includes(c),
  )

  return (
    <div className="rounded-2xl border border-hair bg-surface p-4 shadow-card">
      <div className="flex items-start gap-3">
        <ProviderMark provider={integration.provider} label={label} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-ink">{integration.display_name || label}</span>
            <Badge tone="neutral">{integration.env}</Badge>
            {!integration.enabled && <Badge tone="warning">paused</Badge>}
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-muted">
            {Object.entries(integration.config)
              .map(([k, v]) => `${k}=${v}`)
              .join(' · ') || 'no config'}
          </div>
        </div>
      </div>

      {/* Capabilities, each with its own last-run state. */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {integration.capabilities.map((capability) => {
          const health = integration.health.find((h) => h.capability === capability)
          const tone = health?.status === 'error' ? 'danger' : health?.status === 'ok' ? 'success' : 'info'
          const suffix = health?.last_run_at ? ` · ${timeAgo(health.last_run_at)}` : ''
          return (
            <span key={capability} title={health?.error ?? undefined}>
              <Badge tone={tone}>
                {capability}
                {health?.status === 'ok' && health.item_count != null
                  ? ` · ${health.item_count}`
                  : ''}
                {suffix}
              </Badge>
            </span>
          )
        })}
        {inactive.map((capability) => (
          <span key={capability} title={`${label} supports this — enable it by editing`}>
            <Badge tone="neutral">{capability} · off</Badge>
          </span>
        ))}
      </div>

      {integration.health.some((h) => h.status === 'error') && (
        <p className="mt-2 line-clamp-2 text-xs text-sev-critical">
          {integration.health.find((h) => h.status === 'error')?.error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Button variant="ghost" disabled={busy !== null} onClick={test}>
          {result?.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} className="opacity-0" />}
          {busy === 'test' ? 'Testing…' : 'Test'}
        </Button>
        {canPoll && (
          <Button variant="ghost" disabled={busy !== null} onClick={refresh}>
            <RefreshCw size={14} /> {busy === 'refresh' ? 'Polling…' : 'Refresh'}
          </Button>
        )}
        <Button variant="ghost" disabled={busy !== null} onClick={togglePause}>
          {integration.enabled ? <PauseCircle size={14} /> : <PlayCircle size={14} />}
          {integration.enabled ? 'Pause' : 'Resume'}
        </Button>
        <Button variant="ghost" disabled={busy !== null} onClick={onEdit}>
          <Pencil size={14} /> Edit
        </Button>
        <Button variant="ghost" disabled={busy !== null} onClick={remove}>
          <Trash2 size={14} /> Delete
        </Button>
      </div>

      {result && (
        <p className={`mt-2 text-xs ${result.ok ? 'text-sev-low' : 'text-sev-critical'}`}>
          {result.text}
        </p>
      )}
    </div>
  )
}
