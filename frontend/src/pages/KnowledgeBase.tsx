import type { DashboardData } from '../lib/useDashboard'
import { DocumentList } from '../features/documents/DocumentList'

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
  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in">
        <DocumentList
          rows={data.docs}
          loading={data.loading}
          error={data.error}
          unreachable={data.unreachable}
          query={query}
          onRetry={onRetry}
          onNew={onNew}
        />
      </div>
    </div>
  )
}
