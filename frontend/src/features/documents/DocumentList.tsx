import { BookOpen, FileText, Plus } from 'lucide-react'
import type { DocumentSummary } from '../../lib/types'
import { timeAgo } from '../../lib/format'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'

export function DocumentList({
  rows,
  loading,
  error,
  unreachable,
  query,
  onRetry,
  onNew,
}: {
  rows: DocumentSummary[]
  loading: boolean
  error: string | null
  unreachable: boolean
  query: string
  onRetry: () => void
  onNew: () => void
}) {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? rows.filter((d) =>
        [d.title, d.service, d.source_type, ...d.tags].some((v) => (v ?? '').toLowerCase().includes(q)),
      )
    : rows

  if (error) return <ErrorState detail={error} unreachable={unreachable} onRetry={onRetry} />
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((k) => (
          <Skeleton key={k} className="h-40 rounded-2xl" />
        ))}
      </div>
    )
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={BookOpen}
        title="No documents indexed"
        hint="Add a runbook, postmortem, or architecture note so the AI can retrieve and cite it during triage."
        action={
          <Button onClick={onNew}>
            <Plus size={16} /> New document
          </Button>
        }
      />
    )
  }
  if (filtered.length === 0) {
    return (
      <EmptyState icon={BookOpen} title="No matching documents" hint="Try a different search term." />
    )
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {filtered.map((d) => (
        <div
          key={d.id}
          className="group rounded-2xl border border-hair bg-surface p-5 shadow-card transition duration-300 hover:-translate-y-0.5 hover:shadow-card-hover"
        >
          <div className="flex items-start justify-between gap-3">
            <span
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
              style={{ background: 'color-mix(in srgb, var(--info) 15%, transparent)', color: 'var(--info)' }}
            >
              <FileText size={18} />
            </span>
            <Badge tone="info">{d.source_type}</Badge>
          </div>
          <h3 className="mt-3 line-clamp-2 font-display text-sm font-bold text-ink">{d.title}</h3>
          <div className="mt-2 flex items-center gap-1.5 text-xs text-muted">
            <span className="font-semibold tabular-nums text-ink-2">{d.chunk_count}</span> chunks
            {d.service && (
              <>
                <span aria-hidden>·</span>
                <span className="truncate">{d.service}</span>
              </>
            )}
          </div>
          {d.tags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {d.tags.map((t) => (
                <span
                  key={t}
                  className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-ink-2"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
          <div className="mt-3 border-t border-hair pt-2.5 text-[11px] text-muted">
            Indexed {timeAgo(d.created_at)}
          </div>
        </div>
      ))}
    </div>
  )
}
