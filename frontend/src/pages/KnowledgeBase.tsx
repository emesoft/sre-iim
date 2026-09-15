import { useMemo, useState } from 'react'
import { ChevronDown, Search } from 'lucide-react'
import { api, errText } from '../lib/api'
import type { SeedDocumentsResponse } from '../lib/types'
import type { DashboardData } from '../lib/useDashboard'
import { DocumentList } from '../features/documents/DocumentList'
import { DocumentDetailModal } from '../features/documents/DocumentDetailModal'
import { Button } from '../components/ui/Button'

const ALL = '__all__'
const SHARED = '__shared__'

export function KnowledgeBase({
  data,
  query,
  onRetry,
  onNew,
  canMutate,
}: {
  data: DashboardData
  query: string
  onRetry: () => void
  onNew: () => void
  canMutate: boolean
}) {
  // The document list and the detail modal share the same underlying data, so a save/delete
  // inside the modal reuses the page's existing refetch (`onRetry`) rather than a separate one.
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [scope, setScope] = useState<string>(ALL)
  const [seeding, setSeeding] = useState(false)
  const [seedResult, setSeedResult] = useState<string | null>(null)

  const loadDefaults = async () => {
    setSeeding(true)
    setSeedResult(null)
    try {
      const result = await api.post<SeedDocumentsResponse>('/api/documents/seed', {})
      setSeedResult(
        result.seeded > 0
          ? `Added ${result.seeded} new runbook(s)${result.skipped > 0 ? `, ${result.skipped} already indexed` : ''}.`
          : 'Already up to date — nothing new to add.'
      )
      if (result.seeded > 0) onRetry()
    } catch (e) {
      setSeedResult(errText(e))
    } finally {
      setSeeding(false)
    }
  }

  // Documents with no `service` are shared knowledge: they apply to every project, so they get
  // their own chip rather than being lumped into one project's list or hidden from all of them.
  const shared = useMemo(() => data.docs.filter((d) => !d.service), [data.docs])
  // Ordered by how much is in them, not alphabetically: with a handful of projects the order
  // barely matters, and with fifty the ones worth a visible chip are the ones people actually
  // keep runbooks in.
  const projects = useMemo(() => {
    const counts = new Map<string, number>()
    for (const d of data.docs) {
      if (d.service) counts.set(d.service, (counts.get(d.service) ?? 0) + 1)
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([name]) => name)
  }, [data.docs])
  const rows =
    scope === ALL
      ? data.docs
      : scope === SHARED
        ? shared
        : data.docs.filter((d) => d.service === scope)

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in">
        {(projects.length > 0 || shared.length > 0) && (
          <ScopeBar
            scope={scope}
            onPick={setScope}
            projects={projects}
            counts={{
              [ALL]: data.docs.length,
              [SHARED]: shared.length,
              ...Object.fromEntries(
                projects.map((p) => [p, data.docs.filter((d) => d.service === p).length]),
              ),
            }}
          />
        )}
        {scope !== ALL && scope !== SHARED && shared.length > 0 && (
          // Filtering to one project hides runbooks that genuinely apply to it. Saying so beats
          // silently folding them in, which would make the count disagree with the chip.
          <p className="mb-3 text-xs text-muted">
            {shared.length} shared runbook{shared.length === 1 ? '' : 's'} also appl
            {shared.length === 1 ? 'ies' : 'y'} to {scope} —{' '}
            <button onClick={() => setScope(SHARED)} className="font-semibold text-accent hover:opacity-80">
              view them
            </button>
          </p>
        )}
        {canMutate && (
          <div className="mb-3 flex items-center gap-3">
            <Button variant="ghost" disabled={seeding} onClick={loadDefaults}>
              {seeding ? 'Loading…' : 'Load default runbooks'}
            </Button>
            {seedResult && <p className="text-xs text-muted">{seedResult}</p>}
          </div>
        )}
        <DocumentList
          rows={rows}
          loading={data.loading}
          error={data.error}
          unreachable={data.unreachable}
          query={query}
          onRetry={onRetry}
          onNew={onNew}
          onSelect={setSelectedId}
          canMutate={canMutate}
        />
      </div>
      <DocumentDetailModal
        documentId={selectedId}
        onClose={() => setSelectedId(null)}
        onChanged={onRetry}
        canMutate={canMutate}
      />
    </div>
  )
}

/** How many project chips stay on the row before the rest collapse into a menu. Six fits one line
 * at laptop width; past that the row wraps into a wall and stops being scannable, which is the
 * whole reason to prefer chips over a dropdown in the first place. */
const VISIBLE_PROJECTS = 6

/**
 * One chip per project, plus "All", "Shared", and a menu for the overflow.
 *
 * Chips while the list is short because the counts — which a dropdown hides — are what make it
 * obvious that one project has twenty-seven runbooks and another has one. Past six projects the
 * tail moves into a searchable menu so the row stays a single line at any number of projects. The
 * selected project is always pinned to the row even when it belongs in the tail: a filter you
 * can't see is a filter you forget is on.
 */
function ScopeBar({
  scope,
  onPick,
  projects,
  counts,
}: {
  scope: string
  onPick: (scope: string) => void
  projects: string[]
  counts: Record<string, number>
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  let visible = projects.slice(0, VISIBLE_PROJECTS)
  let overflow = projects.slice(VISIBLE_PROJECTS)
  if (overflow.includes(scope)) {
    // Swap the selected project onto the row, dropping the least-used visible one into the tail.
    visible = [...visible.slice(0, VISIBLE_PROJECTS - 1), scope]
    overflow = projects.slice(VISIBLE_PROJECTS - 1).filter((p) => p !== scope)
  }

  const q = search.trim().toLowerCase()
  const matches = q ? overflow.filter((p) => p.toLowerCase().includes(q)) : overflow

  const chip = (id: string, label: string) => {
    const on = scope === id
    return (
      <button
        key={id}
        onClick={() => onPick(id)}
        aria-current={on ? 'true' : undefined}
        className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
          on
            ? 'border-accent bg-accent-weak text-accent'
            : 'border-hair bg-surface text-ink-2 hover:border-ink-2'
        }`}
      >
        {label}
        <span className={`ml-1.5 ${on ? 'opacity-70' : 'text-muted'}`}>{counts[id] ?? 0}</span>
      </button>
    )
  }

  return (
    <div className="relative mb-3 flex flex-wrap items-center gap-2">
      {chip(ALL, 'All')}
      {visible.map((p) => chip(p, p))}
      {counts[SHARED] > 0 && chip(SHARED, 'Shared')}

      {overflow.length > 0 && (
        <>
          <button
            onClick={() => setOpen((v) => !v)}
            className="flex items-center gap-1 rounded-full border border-dashed border-hair px-3 py-1.5 text-xs font-semibold text-muted transition hover:border-accent hover:text-accent"
          >
            {overflow.length} more
            <ChevronDown size={13} />
          </button>

          {open && (
            <>
              {/* Click anywhere to dismiss — a menu this small doesn't need a focus trap. */}
              <button
                aria-label="Close"
                className="fixed inset-0 z-10 cursor-default"
                onClick={() => setOpen(false)}
              />
              <div className="absolute left-0 top-full z-20 mt-1 w-64 overflow-hidden rounded-xl border border-hair bg-surface shadow-lg">
                <div className="flex items-center gap-2 border-b border-hair px-3 py-2">
                  <Search size={13} className="text-muted" />
                  <input
                    autoFocus
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Find a project"
                    className="w-full bg-transparent text-xs text-ink outline-none"
                  />
                </div>
                <div className="max-h-64 overflow-y-auto">
                  {matches.length === 0 ? (
                    <p className="px-3 py-3 text-center text-xs text-muted">No project matches.</p>
                  ) : (
                    matches.map((p) => (
                      <button
                        key={p}
                        onClick={() => {
                          onPick(p)
                          setOpen(false)
                          setSearch('')
                        }}
                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition hover:bg-plane"
                      >
                        <span className="truncate font-medium text-ink">{p}</span>
                        <span className="shrink-0 text-muted">{counts[p] ?? 0}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
