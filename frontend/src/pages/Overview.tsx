import { AlertTriangle, BookOpen, ChevronRight, Inbox, ShieldAlert, Sparkles } from 'lucide-react'
import type { DashboardData } from '../lib/useDashboard'
import type { IncidentSummary } from '../lib/types'
import { severityMeta } from '../lib/severity'
import { incidentRef, timeAgo } from '../lib/format'
import { StatTile } from '../components/ui/StatTile'
import { Card, CardHeader } from '../components/ui/Card'
import { SeverityBadge } from '../components/ui/SeverityBadge'
import { StatusBadge } from '../components/ui/StatusBadge'
import { Skeleton } from '../components/ui/Skeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorState } from '../components/ui/ErrorState'
import { ActivityFeed } from '../components/ActivityFeed'

export function Overview({
  data,
  query,
  onOpenIncident,
  onViewAll,
  onRetry,
}: {
  data: DashboardData
  query: string
  onOpenIncident: (id: string) => void
  onViewAll: () => void
  onRetry: () => void
}) {
  const { incidents, docs, loading, error } = data

  const q = query.trim().toLowerCase()
  const matches = (i: IncidentSummary) =>
    !q || [i.service, i.summary, i.status, i.fingerprint].some((v) => (v ?? '').toLowerCase().includes(q))
  const recent = incidents.filter(matches).slice(0, 6)

  const total = incidents.length
  const urgent = incidents.filter((i) => severityMeta(i.severity).urgent).length
  const analyzed = incidents.filter((i) => severityMeta(i.severity).key !== 'unknown').length
  const chunks = docs.reduce((s, d) => s + d.chunk_count, 0)
  const coverage = total ? Math.round((analyzed / total) * 100) : 0

  if (error) {
    return (
      <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
        <ErrorState
          detail={error}
          unreachable={data.unreachable}
          onRetry={onRetry}
          className="animate-in mt-2"
        />
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="space-y-5">
        {/* Stat cards */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {loading ? (
            [0, 1, 2, 3].map((k) => <Skeleton key={k} className="h-[132px] rounded-2xl" />)
          ) : (
            <>
              <div className="animate-in" style={{ animationDelay: '0ms' }}>
                <StatTile
                  label="Active incidents"
                  value={total}
                  icon={AlertTriangle}
                  accent="var(--sev-critical)"
                  badge={
                    urgent > 0
                      ? { text: 'Action required', tone: 'danger' }
                      : { text: 'All clear', tone: 'success' }
                  }
                />
              </div>
              <div className="animate-in" style={{ animationDelay: '60ms' }}>
                <StatTile
                  label="Needs attention"
                  value={urgent}
                  icon={ShieldAlert}
                  accent="var(--sev-high)"
                  badge={urgent > 0 ? { text: 'Critical + High', tone: 'warning' } : undefined}
                />
              </div>
              <div className="animate-in" style={{ animationDelay: '120ms' }}>
                <StatTile
                  label="Knowledge docs"
                  value={docs.length}
                  icon={BookOpen}
                  accent="var(--info)"
                  badge={{ text: `${chunks} chunk${chunks === 1 ? '' : 's'}`, tone: 'info' }}
                />
              </div>
              <div className="animate-in" style={{ animationDelay: '180ms' }}>
                <StatTile
                  label="AI analyses"
                  value={analyzed}
                  icon={Sparkles}
                  accent="var(--purple)"
                  badge={{ text: total ? `${coverage}% coverage` : 'Ready', tone: 'purple' }}
                />
              </div>
            </>
          )}
        </div>

        {/* Recent incidents + activity */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.7fr_1fr]">
          <Card className="animate-in p-5 md:p-6">
            <CardHeader
              title="Recent incidents"
              action={
                total > 0 ? (
                  <button
                    onClick={onViewAll}
                    className="text-sm font-semibold text-accent transition hover:opacity-80"
                  >
                    View all
                  </button>
                ) : undefined
              }
            />
            <div className="mt-4">
              {loading ? (
                <div className="space-y-3">
                  {[0, 1, 2, 3].map((k) => (
                    <Skeleton key={k} className="h-12" />
                  ))}
                </div>
              ) : recent.length === 0 ? (
                <EmptyState
                  icon={Inbox}
                  title={q ? 'No matching incidents' : 'No incidents yet'}
                  hint={
                    q
                      ? 'Try a different search term.'
                      : 'Ingest one with New incident to see AI triage land here.'
                  }
                  className="border-0 py-10"
                />
              ) : (
                <ul className="-mx-2 space-y-0.5">
                  {recent.map((i) => {
                    const m = severityMeta(i.severity)
                    return (
                      <li key={i.id}>
                        <button
                          onClick={() => onOpenIncident(i.id)}
                          className="group flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left transition hover:bg-surface-2"
                        >
                          <span
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ background: m.color }}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold text-ink">
                              {i.summary || i.service}
                            </div>
                            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
                              <span className="font-mono">{incidentRef(i.id)}</span>
                              <span aria-hidden>·</span>
                              <span className="truncate">{i.service}</span>
                              <span aria-hidden className="hidden sm:inline">
                                ·
                              </span>
                              <span className="hidden sm:inline">{timeAgo(i.created_at)}</span>
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <SeverityBadge severity={i.severity} size="xs" />
                            <span className="hidden md:inline-flex">
                              <StatusBadge status={i.status} />
                            </span>
                            <ChevronRight
                              size={16}
                              className="text-muted transition group-hover:translate-x-0.5"
                            />
                          </div>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </Card>

          <Card className="animate-in p-5 md:p-6">
            <CardHeader title="Activity" />
            <div className="mt-4">
              {loading ? (
                <div className="space-y-4">
                  {[0, 1, 2, 3].map((k) => (
                    <div key={k} className="flex gap-3">
                      <Skeleton className="h-8 w-8 rounded-full" />
                      <div className="flex-1 space-y-1.5">
                        <Skeleton className="h-3 w-2/3" />
                        <Skeleton className="h-3 w-1/3" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <ActivityFeed incidents={incidents} docs={docs} />
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
