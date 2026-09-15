import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Coins,
  Cpu,
  FileSearch,
  FileText,
  Gauge,
  MousePointerClick,
  Sparkles,
  Ticket,
  Trash2,
} from 'lucide-react'
import { api, errText, isUnreachable } from '../../lib/api'
import type { IncidentCreated, IncidentDetail as Detail } from '../../lib/types'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Textarea } from '../../components/ui/Textarea'
import { SeverityBadge } from '../../components/ui/SeverityBadge'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { Eyebrow } from '../../components/ui/Eyebrow'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'
import { incidentRef } from '../../lib/format'
import { useIncidentStream } from '../../lib/useIncidentStream'
import { ChatPanel } from './ChatPanel'

// `source` names an ingest path, not just CloudWatch — a New Relic-polled or webhook-ingested
// incident still lands here with status="new" (see poll_alarms.py / webhooks.py), so the copy
// must not say "CloudWatch" for those.
function unanalyzedHeadline(source: string): string {
  if (source === 'cloudwatch_alarm') return 'Alarm fired — not analyzed yet'
  if (source === 'newrelic_issue') return 'New Relic alert — not analyzed yet'
  if (source === 'webhook') return 'Alert received — not analyzed yet'
  return 'Not analyzed yet'
}

const SOURCE_LABELS: Record<string, string> = {
  cloudwatch_alarm: 'CloudWatch',
  newrelic_issue: 'New Relic',
  webhook: 'Webhook',
  manual: 'Manual',
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

function unanalyzedSourceLabel(source: string): string {
  if (source === 'cloudwatch_alarm') return 'a CloudWatch alarm'
  if (source === 'newrelic_issue') return 'a New Relic alert'
  if (source === 'webhook') return 'an external alert'
  return 'an external event'
}

export function IncidentDetail({
  incidentId,
  onSelectIncident,
  onIncidentChanged,
  canMutate,
  onIncidentDeleted,
}: {
  incidentId: string | null
  onSelectIncident: (id: string) => void
  /** Fired after anything that changes how this incident appears elsewhere — resolving, ticketing,
   * starting an analysis, or an analysis landing. The list beside this pane and the dashboard
   * counters come from a different fetch, so without this they keep showing the stale status. */
  onIncidentChanged: () => void
  canMutate: boolean
  onIncidentDeleted: () => void
}) {
  const [d, setD] = useState<Detail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [unreachable, setUnreachable] = useState(false)
  const [loading, setLoading] = useState(false)

  const fetchDetail = useCallback((id: string) => {
    let alive = true
    setErr(null)
    api
      .get<Detail>(`/api/incidents/${id}`)
      .then((r) => alive && setD(r))
      .catch((e) => {
        if (!alive) return
        setErr(errText(e))
        setUnreachable(isUnreachable(e))
      })
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!incidentId) {
      setD(null)
      setErr(null)
      return
    }
    setD(null)
    setLoading(true)
    return fetchDetail(incidentId)
  }, [incidentId, fetchDetail])

  // While the incident is still analyzing, subscribe to its live progress; once analysis
  // settles (success or failure), re-fetch the authoritative detail rather than hand-building
  // it from the stream payload.
  const analyzing = d?.status === 'analyzing'
  const stream = useIncidentStream(analyzing ? incidentId : null)

  useEffect(() => {
    if (!incidentId || !(stream.result || stream.error)) return
    onIncidentChanged()
    return fetchDetail(incidentId)
  }, [stream.result, stream.error, incidentId, fetchDetail, onIncidentChanged])

  if (!incidentId) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <EmptyState
          icon={MousePointerClick}
          title="Select an incident"
          hint="Pick one from the list to see its AI triage — severity, root cause, recommended action, and the evidence it cited."
          className="border-0"
        />
      </div>
    )
  }
  if (err) {
    return (
      <div className="p-6">
        <ErrorState detail={err} unreachable={unreachable} />
      </div>
    )
  }
  if (loading || !d) return <DetailSkeleton />

  const a = d.analysis
  return (
    <div key={incidentId} className="animate-in mx-auto max-w-3xl space-y-5 p-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Eyebrow>{incidentRef(d.id)}</Eyebrow>
          <h2 className="mt-1.5 font-display text-2xl font-extrabold tracking-tight text-ink">
            {d.headline || d.service}
          </h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{d.service}</Badge>
            {d.env && <Badge tone="neutral">{d.env}</Badge>}
            {a && <SeverityBadge severity={a.severity} />}
            <StatusBadge status={d.status} />
            {a && <Badge tone={a._cache === 'HIT' ? 'success' : 'neutral'}>cache {a._cache}</Badge>}
            <Badge>{sourceLabel(d.source)}</Badge>
            {d.occurrence_count > 1 && (
              <Badge tone="warning">↻ Recurred {d.occurrence_count}×</Badge>
            )}
          </div>
          {d.previous_incident_id && (
            <button
              onClick={() => onSelectIncident(d.previous_incident_id!)}
              className="mt-1.5 text-xs font-semibold text-accent hover:underline"
            >
              View previous occurrence ({incidentRef(d.previous_incident_id)}) →
            </button>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[11px] text-muted">
            fingerprint <span className="text-ink-2">{d.fingerprint}</span>
            {d.ticket_url && (
              <a
                href={d.ticket_url}
                target="_blank"
                rel="noreferrer"
                className="ml-1"
              >
                <Badge tone="purple">ticket ↗</Badge>
              </a>
            )}
          </div>
        </div>
        {canMutate && (
          <div className="flex shrink-0 items-center gap-2">
            {(d.status === 'new' || d.status === 'failed') && (
              <AnalyzeButton
                incidentId={d.id}
                retry={d.status === 'failed'}
                onAnalyzeStarted={() => {
                  setD({ ...d, status: 'analyzing' })
                  onIncidentChanged()
                }}
              />
            )}
            {a && !a.known_issue && !d.ticket_url && (
              <TicketButton
                incident={d}
                onTicketed={(next) => {
                  setD(next)
                  onIncidentChanged()
                }}
              />
            )}
            {d.status !== 'resolved' && (
              <ResolveButton
                incident={d}
                onResolved={(next) => {
                  setD(next)
                  onIncidentChanged()
                }}
              />
            )}
            <DeleteButton incident={d} onDeleted={onIncidentDeleted} />
          </div>
        )}
      </div>

      {/* Known issue */}
      {a?.known_issue && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-2xl border p-4"
          style={{
            borderColor: 'var(--accent-weak)',
            background: 'color-mix(in srgb, var(--accent) 8%, transparent)',
          }}
        >
          <Sparkles size={18} className="shrink-0 text-accent" />
          <p className="flex-1 text-sm text-ink-2">
            <span className="font-semibold text-ink">Known issue</span> — matches a previously
            resolved incident ({Math.round(a.known_issue.similarity * 100)}% similar).
          </p>
          <button
            type="button"
            onClick={() => onSelectIncident(a.known_issue!.incident_id)}
            className="shrink-0 text-sm font-semibold text-accent transition hover:text-accent-strong"
          >
            View case →
          </button>
        </div>
      )}

      {/* Analysis */}
      {d.status === 'new' ? (
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <AlertTriangle size={15} className="text-sev-medium" />
            <h3 className="font-display text-sm font-bold text-ink">
              {unanalyzedHeadline(d.source)}
            </h3>
          </div>
          <p className="mt-3 whitespace-pre-wrap text-sm text-ink-2">
            {typeof d.context.alert === 'string' ? d.context.alert : 'No alert details in context.'}
          </p>
          <p className="mt-3 text-xs text-muted">
            This incident was created automatically from {unanalyzedSourceLabel(d.source)} and
            hasn't been sent to the AI yet — review the raw alert above, then click "Analyze with
            AI" if you want a root-cause analysis.
          </p>
        </Card>
      ) : analyzing && !stream.result && !stream.error ? (
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-accent" />
            <h3 className="font-display text-sm font-bold text-ink">Analyzing…</h3>
          </div>
          <ul className="mt-4 space-y-3">
            {stream.stages.length === 0 ? (
              <li className="text-sm text-muted">Starting…</li>
            ) : (
              stream.stages.map((s, i) => (
                <li key={i} className="flex items-center gap-3 text-sm">
                  <span className="h-2 w-2 shrink-0 rounded-full bg-accent" />
                  <span className="font-medium text-ink">{s.label}</span>
                  {s.detail && <span className="text-xs text-muted">{s.detail}</span>}
                </li>
              ))
            )}
          </ul>
        </Card>
      ) : a ? (
        <Card className="divide-y divide-hair">
          <Section label="Summary">{a.summary}</Section>
          <Section label="Root cause">{a.root_cause}</Section>
          <Section label="Recommended action">
            <RecommendedAction text={a.recommended_action} />
          </Section>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3.5 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <Gauge size={13} /> confidence{' '}
              <span className="font-semibold text-ink-2">{a.confidence ?? 'n/a'}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Cpu size={13} /> <span className="font-mono text-ink-2">{a.model_id}</span>
            </span>
            {/* A cache HIT is always 0/0 by design (no LLM call was made) — the cache badge
                above already communicates that, so only show a token count when it reflects a
                real call (cache MISS), otherwise "0 in / 0 out" reads as a confusing error. */}
            {a._cache === 'MISS' && a.input_tokens !== null && a.output_tokens !== null && (
              <span className="inline-flex items-center gap-1.5">
                <Coins size={13} />
                <span className="font-mono text-ink-2">
                  {a.input_tokens.toLocaleString()} in / {a.output_tokens.toLocaleString()} out
                </span>
              </span>
            )}
          </div>
        </Card>
      ) : d.status === 'failed' ? (
        <Card className="p-5">
          <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
            <span
              className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl"
              style={{
                background: 'color-mix(in srgb, var(--sev-critical) 14%, transparent)',
                color: 'var(--sev-critical)',
              }}
            >
              <AlertTriangle size={26} />
            </span>
            <h3 className="font-display text-base font-bold text-ink">Analysis failed</h3>
            <p className="mt-1.5 max-w-md text-sm leading-relaxed text-ink-2">
              {stream.error ?? d.error_message ?? 'The AI analysis could not complete.'}
            </p>
          </div>
        </Card>
      ) : (
        <Card className="p-5">
          <EmptyState
            icon={FileSearch}
            title="No analysis yet"
            hint="This incident hasn't produced an AI analysis."
            className="border-0 py-6"
          />
        </Card>
      )}

      {/* Evidence */}
      {a && a.evidence.length > 0 && (
        <Card className="p-5">
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-accent" />
            <h3 className="font-display text-sm font-bold text-ink">Evidence</h3>
            <Badge tone="accent">{a.evidence.length}</Badge>
          </div>
          <ul className="mt-3 space-y-1.5">
            {a.evidence.map((e) => (
              <li
                key={e.chunk_id}
                className="flex items-center gap-2.5 rounded-lg bg-surface-2 px-3 py-2 text-sm"
              >
                <Badge tone="info">{e.source_type}</Badge>
                <span className="truncate text-ink">{e.title}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Chat */}
      <ChatPanel incident={d} canChat={canMutate} key={d.id} />

      {/* Raw context */}
      <details className="group rounded-2xl border border-hair bg-surface">
        <summary className="cursor-pointer px-5 py-3.5 text-[11px] font-bold uppercase tracking-[0.14em] text-muted transition hover:text-ink-2">
          Raw context
        </summary>
        <pre className="max-h-96 overflow-auto border-t border-hair p-4 font-mono text-xs leading-relaxed text-ink-2">
          {JSON.stringify(d.context, null, 2)}
        </pre>
      </details>
    </div>
  )
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="px-5 py-4">
      <Eyebrow>{label}</Eyebrow>
      <div className="mt-1.5 whitespace-pre-wrap leading-relaxed text-ink">{children}</div>
    </div>
  )
}

/** Splits "1. foo\n2. bar" into ["foo", "bar"]; returns null for anything that isn't a
 * multi-step numbered list (a single plain-sentence action, or older un-numbered analyses),
 * so those still render as a normal paragraph. */
function parseSteps(text: string): string[] | null {
  const steps = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.match(/^\d+[.)]\s*(.+)$/)?.[1])
    .filter((step): step is string => Boolean(step))
  return steps.length >= 2 ? steps : null
}

function RecommendedAction({ text }: { text: string }) {
  const steps = parseSteps(text)
  if (!steps) return <>{text}</>
  return (
    <ol className="space-y-2">
      {steps.map((step, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11px] font-bold text-accent">
            {i + 1}
          </span>
          <span className="flex-1">{step}</span>
        </li>
      ))}
    </ol>
  )
}

function AnalyzeButton({
  incidentId,
  retry,
  onAnalyzeStarted,
}: {
  incidentId: string
  retry?: boolean
  onAnalyzeStarted: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const analyze = () => {
    setLoading(true)
    setErr(null)
    api
      .post<IncidentCreated>(`/api/incidents/${incidentId}/analyze`, {})
      .then(onAnalyzeStarted)
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={analyze} disabled={loading}>
        <Sparkles size={15} /> {loading ? 'Starting…' : retry ? 'Retry analysis' : 'Analyze with AI'}
      </Button>
      {err && <p className="max-w-[220px] text-right text-xs text-sev-critical">{err}</p>}
    </div>
  )
}

function TicketButton({
  incident,
  onTicketed,
}: {
  incident: Detail
  onTicketed: (next: Detail) => void
}) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const create = () => {
    setLoading(true)
    setErr(null)
    api
      .post<Detail>(`/api/incidents/${incident.id}/ticket`, {})
      .then(onTicketed)
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="ghost" onClick={create} disabled={loading}>
        <Ticket size={15} /> {loading ? 'Creating…' : 'Create ADO ticket'}
      </Button>
      {err && <p className="max-w-[220px] text-right text-xs text-sev-critical">{err}</p>}
    </div>
  )
}

function ResolveButton({
  incident,
  onResolved,
}: {
  incident: Detail
  onResolved: (next: Detail) => void
}) {
  const [open, setOpen] = useState(false)
  const [notes, setNotes] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!notes.trim()) return
    setLoading(true)
    setErr(null)
    api
      .post<Detail>(`/api/incidents/${incident.id}/resolve`, { resolution_notes: notes.trim() })
      .then((next) => {
        onResolved(next)
        setOpen(false)
      })
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }

  return (
    <>
      <Button variant="ghost" onClick={() => setOpen(true)}>
        <CheckCircle2 size={15} /> Mark resolved
      </Button>
      <Modal open={open} title="Mark resolved" onClose={() => setOpen(false)}>
        <p className="text-sm text-ink-2">
          {incident.analysis
            ? 'Saves this case (summary, root cause, fix) so a similar incident later is flagged as a known issue.'
            : "This incident has no AI analysis yet, so there's nothing to save as a known issue — it'll just be closed out."}
        </p>
        <form onSubmit={submit} className="space-y-3">
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder={
              incident.analysis
                ? 'How was this fixed? (e.g. rolled back to 1.7.9 and bumped container heap size)'
                : 'Why is this being closed? (e.g. false alarm, duplicate, not actionable)'
            }
            rows={4}
            autoFocus
          />
          {err && <p className="text-xs text-sev-critical">{err}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={loading || !notes.trim()}>
              {loading ? 'Saving…' : 'Save & resolve'}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  )
}

function DeleteButton({ incident, onDeleted }: { incident: Detail; onDeleted: () => void }) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const submit = () => {
    if (
      !confirm(
        `Delete ${incidentRef(incident.id)} — ${incident.headline || incident.service}? This cannot be undone.`
      )
    ) {
      return
    }
    setLoading(true)
    setErr(null)
    api
      .del(`/api/incidents/${incident.id}`)
      .then(onDeleted)
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="ghost" disabled={loading} onClick={submit}>
        <Trash2 size={15} /> {loading ? 'Deleting…' : 'Delete'}
      </Button>
      {err && <p className="max-w-[220px] text-right text-xs text-sev-critical">{err}</p>}
    </div>
  )
}

function DetailSkeleton() {
  return (
    <div className="mx-auto max-w-3xl space-y-5 p-6">
      <div className="space-y-2">
        <Skeleton className="h-3 w-20" />
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-3 w-48" />
      </div>
      <Skeleton className="h-56 rounded-2xl" />
      <Skeleton className="h-28 rounded-2xl" />
    </div>
  )
}
