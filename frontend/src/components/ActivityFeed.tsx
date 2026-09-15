import { AlertTriangle, FileText, type LucideIcon } from 'lucide-react'
import type { DocumentSummary, IncidentSummary } from '../lib/types'
import { severityMeta } from '../lib/severity'
import { timeAgo } from '../lib/format'

interface Event {
  key: string
  /** Adjacent events sharing this key collapse into one row. Absent = never collapse. */
  groupKey?: string
  count: number
  icon: LucideIcon
  color: string
  action: string
  subject: string
  detail: string
  at: string
}

/**
 * Collapses a run of adjacent events that say the same thing about the same service into one
 * line. A cloud poll opens several incidents from one connection at once, which used to fill the
 * whole feed with identical "Incident ingested / gcm / Pending severity" rows — the repetition
 * was the only thing visible, and the document events below them got pushed out of the list.
 * Only *adjacent* events merge, so the feed stays a true chronology.
 */
function collapseRuns(events: Event[]): Event[] {
  const out: Event[] = []
  for (const e of events) {
    const prev = out[out.length - 1]
    if (prev && prev.groupKey && prev.groupKey === e.groupKey) {
      prev.count += 1
      prev.action = `${prev.count} incidents ingested`
      continue
    }
    out.push({ ...e })
  }
  return out
}

/**
 * A real activity stream synthesised from the two things that actually happen
 * in this system: incidents get ingested and documents get indexed. Sorted
 * newest-first — no fabricated user actions.
 */
export function ActivityFeed({
  incidents,
  docs,
  limit = 7,
}: {
  incidents: IncidentSummary[]
  docs: DocumentSummary[]
  limit?: number
}) {
  const events: Event[] = collapseRuns(
    [
      ...incidents.map((i) => {
        const m = severityMeta(i.severity)
        return {
          key: `i-${i.id}`,
          groupKey: `i-${i.service}-${m.label}`,
          count: 1,
          icon: AlertTriangle,
          color: m.color,
          action: 'Incident ingested',
          subject: i.service,
          detail: `${m.label} severity`,
          at: i.created_at,
        }
      }),
      ...docs.map((d) => ({
        key: `d-${d.id}`,
        count: 1,
        icon: FileText,
        color: 'var(--info)',
        action: 'Document indexed',
        subject: d.title,
        detail: `${d.chunk_count} chunk${d.chunk_count === 1 ? '' : 's'} · ${d.source_type}`,
        at: d.created_at,
      })),
    ].sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()),
  ).slice(0, limit)

  if (events.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">No activity yet.</p>
  }

  return (
    <ol className="relative">
      {events.map((e, idx) => {
        const Icon = e.icon
        const last = idx === events.length - 1
        return (
          <li key={e.key} className="relative flex gap-3 pb-5 last:pb-0">
            {!last && (
              <span className="absolute left-4 top-9 h-[calc(100%-2rem)] w-px -translate-x-1/2 bg-hair" />
            )}
            <span
              className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
              style={{ background: `color-mix(in srgb, ${e.color} 15%, transparent)`, color: e.color }}
            >
              <Icon size={15} />
            </span>
            <div className="min-w-0 pt-0.5">
              <div className="text-sm font-semibold text-ink">{e.action}</div>
              <div className="truncate text-sm text-ink-2">{e.subject}</div>
              <div className="mt-0.5 text-xs text-muted">
                {e.detail} · {timeAgo(e.at)}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
