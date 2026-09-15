import { ChevronRight, Folder } from 'lucide-react'
import type { ProjectRollup } from '../lib/types'
import { Card, CardHeader } from './ui/Card'
import { EmptyState } from './ui/EmptyState'
import { Skeleton } from './ui/Skeleton'

/**
 * The dashboard's index of projects, and the reason the dashboard survives a busy account: it
 * renders one row per project (a handful) instead of one row per incident (potentially hundreds),
 * so its size is bounded by how many things you run, not by how badly they're behaving.
 *
 * Clicking through opens the incident list already filtered to that project — which is the
 * "a page per project" idea without a second set of routes to keep in sync.
 */
export function ProjectBoard({
  projects,
  loading,
  onOpenProject,
}: {
  projects: ProjectRollup[]
  loading: boolean
  onOpenProject: (project: string) => void
}) {
  return (
    <Card className="h-full min-w-0 p-5 md:p-6">
      <CardHeader title="Projects" />
      <div className="mt-4">
        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((k) => (
              <Skeleton key={k} className="h-12" />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            icon={Folder}
            title="No projects yet"
            hint="Connect a cloud account on Settings, or ingest an incident."
            className="border-0 py-10"
          />
        ) : (
          <>
            {/* The three numbers travel together as one right-hand cluster rather than as columns
                spread to the card's full width. Stretched apart, a row read as a name at one edge
                and digits at the other with a void between them — technically a table, and stiff to
                look at. Grouped, the eye moves once. */}
            <div className="flex items-center gap-3 px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted">
              <span className="flex-1">Project</span>
              <span className="flex shrink-0 items-center gap-4">
                <span className="w-9 text-right">Open</span>
                <span className="w-9 text-right">Urgent</span>
                <span className="w-12 text-right">Untriaged</span>
              </span>
              <span className="hidden w-4 lg:block" />
            </div>
            <ul className="divide-y divide-hair">
              {projects.map((p) => (
                <li key={p.project}>
                  <button
                    onClick={() => onOpenProject(p.project)}
                    className="group flex w-full items-center gap-3 rounded-xl px-2 py-3 text-left transition hover:bg-surface-2"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-ink">
                        {p.project}
                      </span>
                      {p.top_headline && (
                        <span className="mt-0.5 block truncate text-xs text-muted">
                          {p.top_headline}
                        </span>
                      )}
                    </span>
                    <span className="flex shrink-0 items-center gap-4">
                      <span className="w-9 text-right font-display text-base font-bold tabular-nums text-ink">
                        {p.open}
                      </span>
                      {/* A zero is the absence of a problem, so it stays quiet: full weight and
                          colour are spent only where there is something to act on. */}
                      <span
                        className="w-9 text-right font-display text-base tabular-nums"
                        style={{
                          color: p.urgent > 0 ? 'var(--sev-critical)' : 'var(--muted)',
                          fontWeight: p.urgent > 0 ? 700 : 400,
                        }}
                      >
                        {p.urgent}
                      </span>
                      <span
                        className="w-12 text-right font-display text-base tabular-nums"
                        style={{
                          color: p.untriaged > 0 ? 'var(--sev-medium)' : 'var(--muted)',
                          fontWeight: p.untriaged > 0 ? 700 : 400,
                        }}
                      >
                        {p.untriaged}
                      </span>
                    </span>
                    <ChevronRight
                      size={16}
                      className="hidden w-4 shrink-0 text-muted opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100 lg:block"
                    />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Card>
  )
}
