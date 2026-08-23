import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { DocumentDetail } from '../../lib/types'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'

export function DocumentDetailModal({
  documentId,
  onClose,
}: {
  documentId: string | null
  onClose: () => void
}) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!documentId) return
    setDoc(null)
    setError(null)
    api
      .get<DocumentDetail>(`/api/documents/${documentId}`)
      .then(setDoc)
      .catch((e) => setError(errText(e)))
  }, [documentId])

  return (
    <Modal open={documentId !== null} title={doc?.title ?? 'Document'} onClose={onClose}>
      {error && <p className="text-sm text-sev-critical">{error}</p>}
      {!error && !doc && <p className="text-sm text-muted">Loading…</p>}
      {doc && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone="info">{doc.source_type}</Badge>
            {doc.service && <span>{doc.service}</span>}
            {doc.tags.map((t) => (
              <span key={t} className="rounded-full bg-surface-2 px-2 py-0.5 text-ink-2">
                {t}
              </span>
            ))}
          </div>
          <pre className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap rounded-lg bg-plane p-3 text-sm text-ink-2">
            {doc.content}
          </pre>
        </div>
      )}
    </Modal>
  )
}
