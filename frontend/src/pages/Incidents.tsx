import { useEffect, useState } from 'react'
import { useIncidentLane, type LaneId } from '../lib/useIncidentLane'
import { IncidentTable } from '../features/incidents/IncidentTable'
import { IncidentDetail } from '../features/incidents/IncidentDetail'
import { Drawer } from '../components/ui/Drawer'

export function Incidents({
  query,
  refreshKey,
  projectFilter,
  onProjectFilterApplied,
  selectedId,
  onSelect,
  onRetry,
  onIncidentChanged,
  canMutate,
  onIncidentDeleted,
}: {
  query: string
  /** Bumped by the App after any mutation; re-fetches the current lane. */
  refreshKey: number
  /** A project to scope the list to, set when arriving from the dashboard's project board. */
  projectFilter: string | null
  onProjectFilterApplied: () => void
  selectedId: string | null
  onSelect: (id: string | null) => void
  onRetry: () => void
  /** Any mutation in the detail pane (resolve / ticket / analysis) — the list and the dashboard
   * counters are a separate fetch, so they only reflect it if something refetches them. */
  onIncidentChanged: () => void
  canMutate: boolean
  onIncidentDeleted: () => void
}) {
  // The lane owns its own fetch rather than filtering the dashboard's page of incidents: the tab
  // says "Needs triage 40", and filtering a 50-row page would show whichever twelve happened to
  // land on it.
  const [lane, setLane] = useState<LaneId>('triage')
  // The project filter lives here, not in the table, because the tab counts are a second request
  // that has to be narrowed by exactly the same value — that split is what let the counts and the
  // rows describe different sets.
  const [project, setProject] = useState<string | null>(null)
  const laneData = useIncidentLane(lane, project, refreshKey)

  // Arriving from the dashboard's project board scopes the page to that project.
  useEffect(() => {
    if (projectFilter === null) return
    setProject(projectFilter)
    onProjectFilterApplied()
  }, [projectFilter, onProjectFilterApplied])

  return (
    <>
      <IncidentTable
        rows={laneData.rows}
        loading={laneData.loading}
        error={laneData.error}
        unreachable={laneData.unreachable}
        query={query}
        lane={lane}
        laneCounts={laneData.laneCounts}
        onLaneChange={setLane}
        project={project}
        projects={laneData.projects}
        onProjectChange={setProject}
        selectedId={selectedId}
        onSelect={onSelect}
        onRetry={onRetry}
        canMutate={canMutate}
      />

      {/* Over the table rather than beside it: closing puts the reader back at the same row in the
          same scroll position, which is what makes working down a queue bearable. */}
      <Drawer open={selectedId !== null} onClose={() => onSelect(null)}>
        <IncidentDetail
          incidentId={selectedId}
          onSelectIncident={onSelect}
          onIncidentChanged={onIncidentChanged}
          canMutate={canMutate}
          onIncidentDeleted={onIncidentDeleted}
        />
      </Drawer>
    </>
  )
}
