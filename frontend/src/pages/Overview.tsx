import { AlertTriangle, BookOpen, ChevronRight, Inbox, Sparkles } from 'lucide-react'
import type { DashboardData } from '../lib/useDashboard'
import type { IncidentSummary } from '../lib/types'
import { severityMeta } from '../lib/severity'
import { incidentRef, timeAgo } from '../lib/format'
import { AttentionCard } from '../components/AttentionCard'
import { StatStack } from '../components/StatStack'
import { ProjectBoard } from '../components/ProjectBoard'
import { NoisyAlarms } from '../components/NoisyAlarms'
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
  onOpenProject,
  onViewAll,
  onRetry,
}: {
  data: DashboardData
  query: string
  onOpenIncident: (id: string) => void
  onOpenProject: (project: string) => void
  onViewAll: () => void
  onRetry: () => void
}) {
  const { incidents, docs, rollup, loading, error } = data

  const q = query.trim().toLowerCase()
  const matches = (i: IncidentSummary) =>
    !q ||
    [i.service, i.summary, i.headline, i.status, i.fingerprint].some((v) =>
      (v ?? '').toLowerCase().includes(q),
    )
  // Only the latest occurrence of a recurring alarm — see the same collapse in IncidentTable.
  const supersededIds = new Set(
    incidents.map((i) => i.previous_incident_id).filter((id): id is string => Boolean(id)),
  )
  const current = incidents.filter((i) => !supersededIds.has(i.id))
  const recent = current.filter(matches).slice(0, 6)

  // Counts come from the rollup (aggregated in SQL), never from `incidents` — that's one page of
  // rows, so counting it under-reports as soon as there are more incidents than the page holds.
  // The urgent *rows* still come from the page; the card reconciles the two when they disagree.
  const total = rollup?.active ?? 0
  const urgentCount = rollup?.urgent ?? 0
  const untriaged = rollup?.untriaged ?? 0
  const newLast24h = rollup?.new_last_24h ?? 0
  const urgentRows = current.filter(
    (i) => i.status !== 'resolved' && severityMeta(i.severity).urgent,
  )
  // Open and never analysed. Fills the attention card when little is urgent — an unassessed
  // incident is the honest next thing to look at, not padding.
  const untriagedRows = current.filter((i) => i.status !== 'resolved' && !i.severity)
  const chunks = docs.reduce((s, d) => s + d.chunk_count, 0)

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
        {/* Triage strip: the one card that demands a decision, plus its supporting numbers. */}
        {/* Cards in a row share a height — that shared baseline is most of what makes a grid read
            as deliberate rather than assembled. The fix for a half-empty card is its content
            centring inside it, not the card shrinking away from its neighbour. */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.55fr_1fr]">
          <div className="animate-in h-full">
            <AttentionCard
              urgent={urgentRows}
              untriaged={untriagedRows}
              urgentCount={urgentCount}
              activeCount={total}
              loading={loading}
              onOpenIncident={onOpenIncident}
              onViewAll={onViewAll}
            />
          </div>
          <div className="animate-in h-full" style={{ animationDelay: '80ms' }}>
            <StatStack
              loading={loading}
              items={[
                {
                  label: 'Active incidents',
                  value: total,
                  icon: AlertTriangle,
                  accent: 'var(--sev-critical)',
                  badge:
                    newLast24h > 0
                      ? { text: `+${newLast24h} in 24h`, tone: 'neutral' }
                      : undefined,
                },
                {
                  label: 'Untriaged',
                  value: untriaged,
                  icon: Sparkles,
                  accent: 'var(--purple)',
                  badge:
                    untriaged > 0
                      ? { text: 'no AI analysis yet', tone: 'warning' }
                      : { text: 'all analysed', tone: 'success' },
                },
                {
                  label: 'Knowledge docs',
                  value: docs.length,
                  icon: BookOpen,
                  accent: 'var(--info)',
                  badge: { text: `${chunks} chunk${chunks === 1 ? '' : 's'}`, tone: 'info' },
                },
              ]}
            />
          </div>
        </div>

        {/* The scalable half of the dashboard: one row per project and per repeating alarm, so
            neither block grows with the incident count. */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.55fr_1fr]">
          <div className="animate-in h-full">
            <ProjectBoard
              projects={rollup?.projects ?? []}
              loading={loading}
              onOpenProject={onOpenProject}
            />
          </div>
          <div className="animate-in h-full" style={{ animationDelay: '80ms' }}>
            <NoisyAlarms alarms={rollup?.noisy ?? []} loading={loading} />
          </div>
        </div>

        {/* Recent incidents + activity */}
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1.7fr_1fr]">
          <Card className="animate-in min-w-0 p-5 md:p-6">
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
                    const m = severityMeta(i.severity, i.status)
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
                            {/* headline (the short alarm name) wins over the AI summary here, as
                                in IncidentTable: the summary is a full sentence that just gets
                                ellipsed away in a row this size. `service` is the last resort —
                                every incident from one connection shares it, so on its own it
                                made every row read "gcm". */}
                            <div className="truncate text-sm font-semibold text-ink">
                              {i.headline || i.summary || i.service}
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
                            <SeverityBadge severity={i.severity} status={i.status} size="xs" />
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

          <Card className="animate-in min-w-0 p-5 md:p-6">
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
