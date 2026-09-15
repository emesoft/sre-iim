import { ArrowRight, ShieldAlert } from 'lucide-react'
import type { IncidentSummary } from '../lib/types'
import { severityMeta } from '../lib/severity'
import { incidentRef, timeAgo } from '../lib/format'
import { Button } from './ui/Button'
import { Sparky } from './Sparky'
import { Skeleton } from './ui/Skeleton'

const MAX_ROWS = 3

/**
 * The dashboard's one hero card. The previous layout gave "26 active" and "1 needs attention"
 * identical visual weight, so nothing told the on-call engineer where to look first — this card
 * takes the count that actually demands a decision and puts the offending incidents *in* it, one
 * click from being opened.
 *
 * With nothing urgent it becomes the mascot's home: an explicit, cheerful all-clear beats an
 * empty card showing a zero, and it's the one place a mascot earns its space by carrying state
 * (Sparky's beacon lights up here when the queue isn't empty).
 */
export function AttentionCard({
  urgent,
  untriaged,
  urgentCount,
  activeCount,
  loading,
  onOpenIncident,
  onViewAll,
}: {
  /** Urgent incidents from the fetched page — the rows this card can link to directly. */
  urgent: IncidentSummary[]
  /** Open incidents nobody has analysed yet. Used to fill the card when little or nothing is
   * urgent — see `rows` below. */
  untriaged: IncidentSummary[]
  /** The true total from the rollup, which can exceed `urgent.length` when some urgent incidents
   * fall outside the page that was fetched. The count shown is always this one. */
  urgentCount: number
  activeCount: number
  loading: boolean
  onOpenIncident: (id: string) => void
  onViewAll: () => void
}) {
  if (loading) {
    return <Skeleton className="h-[210px] rounded-2xl" />
  }

  const calm = urgentCount === 0
  const hidden = Math.max(0, urgentCount - Math.min(urgent.length, MAX_ROWS))

  // With one urgent incident this card used to be a single row and a large empty rectangle — the
  // most prominent block on the page, mostly blank. The remainder is filled with incidents nobody
  // has analysed yet, which is the honest answer to "what next": they are unassessed rather than
  // unimportant, and one of them outranking the urgent list is exactly what triage would find.
  const filler = untriaged
    .filter((i) => !urgent.some((u) => u.id === i.id))
    .slice(0, MAX_ROWS - Math.min(urgent.length, MAX_ROWS))
  const rows = [...urgent.slice(0, MAX_ROWS), ...filler]

  return (
    <div
      className="relative overflow-hidden rounded-2xl border bg-surface p-5 shadow-card md:p-6"
      style={{
        borderColor: calm ? 'var(--hair)' : 'color-mix(in srgb, var(--sev-critical) 35%, var(--hair))',
      }}
    >
      {!calm && (
        <span
          className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full blur-3xl"
          style={{ background: 'color-mix(in srgb, var(--sev-critical) 22%, transparent)' }}
        />
      )}

      {calm ? (
        <div className="relative flex items-center gap-5">
          <Sparky size={72} mood="happy" className="shrink-0" />
          <div className="min-w-0">
            <h3 className="font-display text-xl font-extrabold tracking-tight text-ink">
              All clear
            </h3>
            <p className="mt-1 text-sm text-ink-2">
              Nothing on fire right now — no incident is at critical or high severity.
            </p>
            <p className="mt-3 text-xs font-medium text-muted">
              {activeCount} active {activeCount === 1 ? 'incident' : 'incidents'} · none urgent
            </p>
          </div>
        </div>
      ) : (
        <div className="relative">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <Sparky size={40} mood="alert" className="shrink-0" />
              <div>
                <h3 className="font-display text-sm font-bold uppercase tracking-[0.12em] text-ink-2">
                  Needs attention
                </h3>
                <div className="mt-0.5 flex items-baseline gap-2">
                  <span className="font-display text-[2rem] font-extrabold leading-none tabular-nums text-ink">
                    {urgentCount}
                  </span>
                  <span className="text-sm font-medium text-ink-2">
                    critical + high, of {activeCount} active
                  </span>
                </div>
              </div>
            </div>
            <Button
              onClick={() => (urgent[0] ? onOpenIncident(urgent[0].id) : onViewAll())}
              className="shrink-0"
            >
              <ShieldAlert size={15} /> Triage now
            </Button>
          </div>

          <ul className="mt-4 space-y-0.5">
            {rows.map((i, index) => {
              const m = severityMeta(i.severity, i.status)
              const startsFiller = filler.length > 0 && index === rows.length - filler.length
              return (
                <li key={i.id}>
                  {/* Labelled, never silently mixed in: these are unassessed, not urgent, and a
                      reader must not come away thinking the AI called them critical. */}
                  {startsFiller && (
                    <p className="mb-1 mt-3 px-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
                      Not assessed yet
                    </p>
                  )}
                  <button
                    onClick={() => onOpenIncident(i.id)}
                    className="group flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition hover:bg-surface-2"
                  >
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: m.color }}
                    />
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
                      {i.headline || i.summary || i.service}
                    </span>
                    <span className="hidden shrink-0 font-mono text-xs text-muted sm:inline">
                      {incidentRef(i.id)}
                    </span>
                    <span className="hidden shrink-0 text-xs text-muted md:inline">
                      {timeAgo(i.created_at)}
                    </span>
                    <ArrowRight
                      size={15}
                      className="shrink-0 text-muted transition group-hover:translate-x-0.5"
                    />
                  </button>
                </li>
              )
            })}
          </ul>

          {hidden > 0 && (
            <button
              onClick={onViewAll}
              className="mt-1 px-2 text-xs font-semibold text-accent transition hover:opacity-80"
            >
              +{hidden} more urgent
            </button>
          )}
        </div>
      )}
    </div>
  )
}
