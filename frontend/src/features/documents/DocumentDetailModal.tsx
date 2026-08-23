import { useEffect, useState } from 'react'
import { api, ApiError, errText } from '../../lib/api'
import type { DocumentDetail } from '../../lib/types'
import { SOURCE_TYPES } from '../../lib/types'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'
import { Textarea } from '../../components/ui/Textarea'

const inputCls =
  'w-full rounded-lg border border-hair bg-plane p-1.5 text-sm text-ink outline-none focus:border-accent'

export function DocumentDetailModal({
  documentId,
  onClose,
  onChanged,
}: {
  documentId: string | null
  onClose: () => void
  /** Called after a save or delete — the caller re-fetches the document list. */
  onChanged: () => void
}) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)

  const [title, setTitle] = useState('')
  const [sourceType, setSourceType] = useState<string>(SOURCE_TYPES[0])
  const [service, setService] = useState('')
  const [tags, setTags] = useState('')
  const [content, setContent] = useState('')

  useEffect(() => {
    if (!documentId) return
    setDoc(null)
    setError(null)
    setEditing(false)
    api
      .get<DocumentDetail>(`/api/documents/${documentId}`)
      .then(setDoc)
      .catch((e) => setError(errText(e)))
  }, [documentId])

  const startEdit = () => {
    if (!doc) return
    setTitle(doc.title)
    setSourceType(doc.source_type)
    setService(doc.service ?? '')
    setTags(doc.tags.join(', '))
    setContent(doc.content)
    setEditing(true)
  }

  const save = async () => {
    if (!documentId) return
    setError(null)
    setBusy(true)
    try {
      const updated = await api.patch<DocumentDetail>(`/api/documents/${documentId}`, {
        title,
        source_type: sourceType,
        service: service || null,
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        content,
      })
      setDoc(updated)
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : errText(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!documentId) return
    if (!confirm('Delete this document? This cannot be undone.')) return
    setBusy(true)
    try {
      await api.del(`/api/documents/${documentId}`)
      onChanged()
      onClose()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={documentId !== null} title={doc?.title ?? 'Document'} onClose={onClose}>
      {error && <p className="text-sm text-sev-critical">{error}</p>}
      {!error && !doc && <p className="text-sm text-muted">Loading…</p>}

      {doc && !editing && (
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
          <div className="flex justify-end gap-2">
            <Button variant="ghost" disabled={busy} onClick={remove}>
              Delete
            </Button>
            <Button onClick={startEdit}>Edit</Button>
          </div>
        </div>
      )}

      {doc && editing && (
        <div className="flex flex-col gap-3">
          <Field label="Title">
            <input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label="Source type">
            <select
              className={inputCls}
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
            >
              {SOURCE_TYPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Service (optional)">
            <input className={inputCls} value={service} onChange={(e) => setService(e.target.value)} />
          </Field>
          <Field label="Tags (comma-separated)">
            <input className={inputCls} value={tags} onChange={(e) => setTags(e.target.value)} />
          </Field>
          <Field label="Content">
            <Textarea rows={12} value={content} onChange={(e) => setContent(e.target.value)} />
          </Field>
          {error && <p className="text-xs text-sev-critical">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button onClick={save} disabled={busy}>
              {busy ? 'Saving…' : 'Save changes'}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
