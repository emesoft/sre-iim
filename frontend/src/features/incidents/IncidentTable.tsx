import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowDown, ArrowUp, Loader2, Sparkles, X } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { BulkResult, IncidentSummary } from '../../lib/types'
import { severityMeta } from '../../lib/severity'
import { incidentRef, timeAgo } from '../../lib/format'
import { LANES, type LaneId } from '../../lib/useIncidentLane'
import { StatusBadge } from '../../components/ui/StatusBadge'
import { Badge } from '../../components/ui/Badge'
import { Skeleton } from '../../components/ui/Skeleton'
import { EmptyState } from '../../components/ui/EmptyState'
import { ErrorState } from '../../components/ui/ErrorState'

const ALL_PROJECTS = 'All projects'

/** Analyzing several incidents is one paid AI call each, so the backend caps it well below the
 * request limit. Mirrored here to explain the limit before the click rather than after. */
const MAX_BULK_ANALYZE = 10

/** Worst first when sorting by severity. Unanalyzed incidents sort *above* low/closed ones: an
 * incident nobody has triaged is a bigger open question than one already judged minor. */
const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  pending: 2,
  medium: 3,
  low: 4,
  unknown: 5,
  closed: 6,
}

type SortKey = 'severity' | 'incident' | 'project' | 'status' | 'age'

/**
 * The incident queue as a full-width table.
 *
 * Replaced a 340px master/detail column, which is the layout every real incident tool abandons
 * once titles get long: CloudWatch alarm names run past 50 characters and differ only in their
 * tail, so a narrow column showed five distinct alarms as five identical rows. A table has the
 * width to show them whole, the columns to sort by, and a natural home for the select-many
 * checkbox that bulk triage needs.
 */
export function IncidentTable({
  rows,
  loading,
  error,
  unreachable,
  query,
  lane,
  laneCounts,
  onLaneChange,
  project,
  projects,
  onProjectChange,
  selectedId,
  onSelect,
  onRetry,
  canMutate,
}: {
  rows: IncidentSummary[]
  loading: boolean
  error: string | null
  unreachable: boolean
  query: string
  lane: LaneId
  laneCounts: Record<string, number>
  onLaneChange: (lane: LaneId) => void
  /** null = every project the caller can see. Owned by the page, because the tab counts are a
   * second request that has to carry the same value. */
  project: string | null
  projects: string[]
  onProjectChange: (project: string | null) => void
  selectedId: string | null
  onSelect: (id: string) => void
  onRetry: () => void
  canMutate: boolean
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'age', desc: true })
  const [busy, setBusy] = useState(false)
  const [bulkError, setBulkError] = useState<string | null>(null)


  // Switching lanes changes which rows exist, so a selection made in the old one is meaningless —
  // and acting on ids that are no longer on screen is the classic bulk-action accident.
  useEffect(() => {
    setPicked(new Set())
    setBulkError(null)
  }, [lane])

  // A recurring alarm creates a new incident each time it fires — chained via previous_incident_id
  // (see the "↻ Nx" badge). Only the latest occurrence of each chain is listed; the older ones stay
  // reachable from it in the detail view.
  const supersededIds = useMemo(
    () => new Set(rows.map((i) => i.previous_incident_id).filter((id): id is string => Boolean(id))),
    [rows],
  )
  const latest = useMemo(() => rows.filter((i) => !supersededIds.has(i.id)), [rows, supersededIds])

  const q = query.trim().toLowerCase()
  const visible = useMemo(() => {
    // No client-side project filter: the rows arrived already narrowed, by the same `service` the
    // tab counts were narrowed by. Filtering again here would only be able to disagree.
    const searched = q
      ? latest.filter((i) =>
          [i.service, i.headline, i.summary, i.status, i.fingerprint].some((v) =>
            (v ?? '').toLowerCase().includes(q),
          ),
        )
      : latest
    const dir = sort.desc ? -1 : 1
    return [...searched].sort((a, b) => {
      switch (sort.key) {
        case 'severity':
          return (
            (SEVERITY_RANK[severityMeta(a.severity, a.status).key] -
              SEVERITY_RANK[severityMeta(b.severity, b.status).key]) * -dir
          )
        case 'incident':
          return (a.headline ?? a.service).localeCompare(b.headline ?? b.service) * -dir
        case 'project':
          return `${a.service}${a.env}`.localeCompare(`${b.service}${b.env}`) * -dir
        case 'status':
          return a.status.localeCompare(b.status) * -dir
        default:
          return (Date.parse(a.created_at) - Date.parse(b.created_at)) * dir
      }
    })
  }, [latest, q, sort])

  const allPicked = visible.length > 0 && visible.every((i) => picked.has(i.id))

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const runBulk = async (action: 'resolve' | 'analyze') => {
    const ids = [...picked]
    if (action === 'analyze' && ids.length > MAX_BULK_ANALYZE) {
      setBulkError(`Analyze runs one AI call per incident — pick at most ${MAX_BULK_ANALYZE}.`)
      return
    }
    setBusy(true)
    setBulkError(null)
    try {
      const result = await api.post<BulkResult>('/api/incidents/bulk', {
        action,
        incident_ids: ids,
      })
      // `skipped` is ids that vanished or aren't in this user's projects — saying so beats
      // reporting a clean success for a partial one.
      if (result.skipped > 0)
        setBulkError(`${result.skipped} skipped (no longer available to you).`)
      setPicked(new Set())
      onRetry()
    } catch (e) {
      setBulkError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  const sortBy = (key: SortKey) =>
    setSort((s) => ({ key, desc: s.key === key ? !s.desc : key === 'age' }))

  const th = (key: SortKey, label: string, className = '') => (
    <th className={`px-3 py-2 font-semibold ${className}`}>
      <button
        onClick={() => sortBy(key)}
        className="flex items-center gap-1 transition hover:text-ink"
        aria-label={`Sort by ${label}`}
      >
        {label}
        {sort.key === key &&
          (sort.desc ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}
      </button>
    </th>
  )

  const header = (
    <div className="sticky top-0 z-10 bg-plane">
      <div className="flex items-center gap-1 overflow-x-auto border-b border-hair px-4 md:px-8">
        {LANES.map(({ id, label }) => {
          const on = id === lane
          return (
            <button
              key={id}
              onClick={() => onLaneChange(id)}
              aria-current={on ? 'page' : undefined}
              className={`-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-semibold transition ${
                on ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink-2'
              }`}
            >
              {label}
              <span className={`ml-1.5 text-xs ${on ? 'text-accent' : 'text-muted'}`}>
                {laneCounts[id] ?? 0}
              </span>
            </button>
          )
        })}
        {projects.length > 1 && (
          <select
            value={project ?? ALL_PROJECTS}
            onChange={(e) =>
              onProjectChange(e.target.value === ALL_PROJECTS ? null : e.target.value)
            }
            className="my-1 ml-auto shrink-0 rounded-lg border border-hair bg-surface px-2 py-1 text-xs text-ink-2"
          >
            <option value={ALL_PROJECTS}>{ALL_PROJECTS}</option>
            {projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        )}
      </div>

      {picked.size > 0 && canMutate && (
        <div className="flex flex-wrap items-center gap-2 border-b border-hair bg-surface px-4 py-2 md:px-8">
          <span className="text-xs font-semibold text-ink">{picked.size} selected</span>
          <div className="ml-auto flex items-center gap-1.5">
            {lane !== 'done' && (
              <>
                <button
                  disabled={busy}
                  onClick={() => runBulk('analyze')}
                  className="flex items-center gap-1 rounded-lg border border-hair px-2.5 py-1 text-xs font-semibold text-ink-2 transition hover:border-accent hover:text-accent disabled:opacity-50"
                >
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                  Analyze
                </button>
                <button
                  disabled={busy}
                  onClick={() => runBulk('resolve')}
                  className="rounded-lg border border-hair px-2.5 py-1 text-xs font-semibold text-ink-2 transition hover:border-accent hover:text-accent disabled:opacity-50"
                >
                  Resolve
                </button>
              </>
            )}
            <button
              onClick={() => setPicked(new Set())}
              className="rounded-lg p-1 text-muted transition hover:text-ink"
              aria-label="Clear selection"
            >
              <X size={13} />
            </button>
          </div>
          {bulkError && <p className="w-full text-xs text-sev-critical">{bulkError}</p>}
        </div>
      )}
    </div>
  )

  if (error)
    return (
      <div className="p-4 md:p-8">
        <ErrorState detail={error} unreachable={unreachable} onRetry={onRetry} />
      </div>
    )

  return (
    <div className="h-full overflow-y-auto">
      {header}

      {loading ? (
        <div className="space-y-2 p-4 md:p-8">
          {[0, 1, 2, 3, 4, 5].map((k) => (
            <Skeleton key={k} className="h-11 rounded-lg" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title={q ? 'No matches' : 'Nothing here'}
          hint={
            q
              ? 'Adjust your search term.'
              : lane === 'triage'
                ? 'Everything has been looked at — nice.'
                : 'Incidents show up here as they move through triage.'
          }
          className="m-6 border-0"
        />
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="border-b border-hair text-xs uppercase tracking-wide text-muted">
            <tr>
              {canMutate && (
                <th className="w-9 px-4 py-2 md:pl-8">
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={allPicked}
                    onChange={() =>
                      setPicked(allPicked ? new Set() : new Set(visible.map((i) => i.id)))
                    }
                    className="accent-[var(--accent)]"
                  />
                </th>
              )}
              {th('severity', 'Sev', 'w-24')}
              {th('incident', 'Incident')}
              {th('project', 'Project', 'w-40')}
              {th('status', 'Status', 'w-32')}
              {th('age', 'Age', 'w-24 md:pr-8')}
            </tr>
          </thead>
          <tbody>
            {visible.map((i) => {
              const m = severityMeta(i.severity, i.status)
              const active = selectedId === i.id
              const title = i.headline || i.service
              return (
                <tr
                  key={i.id}
                  onClick={() => onSelect(i.id)}
                  aria-current={active ? 'true' : undefined}
                  className={`cursor-pointer border-b border-hair transition last:border-0 ${
                    active ? 'bg-accent-weak' : 'hover:bg-surface-2'
                  }`}
                >
                  {canMutate && (
                    <td className="px-4 py-2.5 md:pl-8" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select ${title}`}
                        checked={picked.has(i.id)}
                        onChange={() => toggle(i.id)}
                        className="accent-[var(--accent)]"
                      />
                    </td>
                  )}
                  <td className="px-3 py-2.5">
                    <span className="flex items-center gap-1.5">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ background: m.color }}
                      />
                      <span className="text-xs font-semibold text-ink-2">{m.label}</span>
                    </span>
                  </td>
                  <td className="min-w-0 px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      {/* The width a table gives is the whole point: these names differ only in
                          their tail, which a narrow column clipped away. */}
                      <span className="truncate font-medium text-ink" title={title}>
                        {title}
                      </span>
                      {i.occurrence_count > 1 && (
                        <span title={`Recurred ${i.occurrence_count} times`}>
                          <Badge tone="warning">↻ {i.occurrence_count}×</Badge>
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted">
                      {i.summary || 'Awaiting analysis…'}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-ink-2">
                    {i.service}
                    {i.env && <span className="text-muted"> · {i.env}</span>}
                    <div className="font-mono text-[10px] text-muted">{incidentRef(i.id)}</div>
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={i.status} />
                  </td>
                  <td className="px-3 py-2.5 text-xs text-muted md:pr-8">
                    {timeAgo(i.created_at)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
