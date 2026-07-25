import type { DashboardData } from '../lib/useDashboard'
import { IncidentList } from '../features/incidents/IncidentList'
import { IncidentDetail } from '../features/incidents/IncidentDetail'

export function Incidents({
  data,
  query,
  selectedId,
  onSelect,
  onRetry,
}: {
  data: DashboardData
  query: string
  selectedId: string | null
  onSelect: (id: string) => void
  onRetry: () => void
}) {
  return (
    <div className="grid h-full grid-cols-[minmax(260px,340px)_1fr] overflow-hidden">
      <aside className="min-h-0 overflow-y-auto border-r border-hair bg-surface/50">
        <IncidentList
          rows={data.incidents}
          loading={data.loading}
          error={data.error}
          unreachable={data.unreachable}
          query={query}
          selectedId={selectedId}
          onSelect={onSelect}
          onRetry={onRetry}
        />
      </aside>
      <section className="min-h-0 overflow-y-auto">
        <IncidentDetail incidentId={selectedId} onSelectIncident={onSelect} />
      </section>
    </div>
  )
}
