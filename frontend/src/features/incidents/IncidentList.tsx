import { AlertTriangle } from 'lucide-react'
import type { IncidentSummary } from '../../lib/types'
import { severityMeta } from '../../lib/severity'
import { incidentRef, timeAgo } from '../../lib/format'
import { SeverityBadge } from '../../components/ui/SeverityBadge'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'

export function IncidentList({
  rows,
  loading,
  error,
  unreachable,
  query,
  selectedId,
  onSelect,
  onRetry,
}: {
  rows: IncidentSummary[]
  loading: boolean
  error: string | null
  unreachable: boolean
  query: string
  selectedId: string | null
  onSelect: (id: string) => void
  onRetry: () => void
}) {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? rows.filter((i) =>
        [i.service, i.summary, i.status, i.fingerprint].some((v) => (v ?? '').toLowerCase().includes(q)),
      )
    : rows

  if (error) {
    return (
      <div className="p-3">
        <ErrorState detail={error} unreachable={unreachable} onRetry={onRetry} />
      </div>
    )
  }
  if (loading) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2, 3, 4].map((k) => (
          <Skeleton key={k} className="h-[86px] rounded-xl" />
        ))}
      </div>
    )
  }
  if (filtered.length === 0) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title={q ? 'No matches' : 'No incidents'}
        hint={q ? 'Adjust your search term.' : 'Create one with New incident.'}
        className="m-3 border-0"
      />
    )
  }

  return (
    <ul className="p-2">
      {filtered.map((i) => {
        const m = severityMeta(i.severity)
        const active = selectedId === i.id
        return (
          <li key={i.id}>
            <button
              onClick={() => onSelect(i.id)}
              aria-current={active ? 'true' : undefined}
              className={`mb-1 w-full rounded-xl border px-3 py-3 text-left transition ${
                active
                  ? 'border-accent bg-accent-weak'
                  : 'border-transparent hover:border-hair hover:bg-surface-2'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: m.color }} />
                  <span className="truncate text-sm font-semibold text-ink">{i.service}</span>
                </div>
                <SeverityBadge severity={i.severity} size="xs" />
              </div>
              <p className="mt-1.5 line-clamp-2 text-xs text-ink-2">
                {i.summary || 'Awaiting analysis…'}
              </p>
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-muted">{incidentRef(i.id)}</span>
                <div className="flex items-center gap-2">
                  <StatusBadge status={i.status} />
                  <span className="text-[10px] text-muted">{timeAgo(i.created_at)}</span>
                </div>
              </div>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
