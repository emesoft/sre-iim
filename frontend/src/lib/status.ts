// Incident lifecycle status → pastel badge tone. The label always renders, so a
// status is never communicated by color alone. Unknown statuses fall back to a
// title-cased neutral pill rather than breaking, since the backend owns the
// exact vocabulary.

export type StatusTone = 'info' | 'warning' | 'success' | 'neutral' | 'accent' | 'danger'

export interface StatusMeta {
  label: string
  tone: StatusTone
}

// This app's own lifecycle only ever sets new/analyzing/analyzed/failed/ticketed/resolved
// (backend/app/domain/incidents/entities.py) — the label mirrors that backend vocabulary
// directly rather than inventing a simplified one, so what you see always matches the real
// status name. The rest are legacy/generic labels kept for any status string this app doesn't
// itself produce.
const TABLE: Record<string, StatusMeta> = {
  new: { label: 'New', tone: 'info' },
  analyzing: { label: 'Analyzing', tone: 'info' },
  analyzed: { label: 'Analyzed', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  ticketed: { label: 'Ticketed', tone: 'accent' },
  resolved: { label: 'Resolved', tone: 'success' },
  open: { label: 'Open', tone: 'warning' },
  investigating: { label: 'Investigating', tone: 'info' },
  triaged: { label: 'Triaged', tone: 'accent' },
  monitoring: { label: 'Monitoring', tone: 'accent' },
  mitigated: { label: 'Mitigated', tone: 'success' },
  closed: { label: 'Closed', tone: 'neutral' },
}

export function statusMeta(status?: string | null): StatusMeta {
  const s = (status ?? '').toLowerCase().trim()
  if (s in TABLE) return TABLE[s]
  const label = s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown'
  return { label, tone: 'neutral' }
}
