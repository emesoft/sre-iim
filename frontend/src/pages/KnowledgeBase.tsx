import { useState } from 'react'
import { api, errText } from '../lib/api'
import type { SeedDocumentsResponse } from '../lib/types'
import type { DashboardData } from '../lib/useDashboard'
import { DocumentList } from '../features/documents/DocumentList'
import { DocumentDetailModal } from '../features/documents/DocumentDetailModal'
import { Button } from '../components/ui/Button'

export function KnowledgeBase({
  data,
  query,
  onRetry,
  onNew,
}: {
  data: DashboardData
  query: string
  onRetry: () => void
  onNew: () => void
}) {
  // The document list and the detail modal share the same underlying data, so a save/delete
  // inside the modal reuses the page's existing refetch (`onRetry`) rather than a separate one.
  const [selectedId, setSelectedId] = useState<string | null>(null)
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

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in">
        <div className="mb-3 flex items-center gap-3">
          <Button variant="ghost" disabled={seeding} onClick={loadDefaults}>
            {seeding ? 'Loading…' : 'Load default runbooks'}
          </Button>
          {seedResult && <p className="text-xs text-muted">{seedResult}</p>}
        </div>
        <DocumentList
          rows={data.docs}
          loading={data.loading}
          error={data.error}
          unreachable={data.unreachable}
          query={query}
          onRetry={onRetry}
          onNew={onNew}
          onSelect={setSelectedId}
        />
      </div>
      <DocumentDetailModal documentId={selectedId} onClose={() => setSelectedId(null)} onChanged={onRetry} />
    </div>
  )
}
