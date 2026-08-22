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
// (backend/app/domain/incidents/entities.py); those six map to the simpler New/In Progress/
// Pending/Failed/Closed vocabulary below. The rest are legacy/generic labels kept for any status
// string this app doesn't itself produce.
const TABLE: Record<string, StatusMeta> = {
  new: { label: 'New', tone: 'info' },
  analyzing: { label: 'In Progress', tone: 'info' },
  analyzed: { label: 'Pending', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  ticketed: { label: 'In Progress', tone: 'accent' },
  resolved: { label: 'Closed', tone: 'success' },
  open: { label: 'Open', tone: 'warning' },
  investigating: { label: 'In Progress', tone: 'info' },
  triaged: { label: 'Pending', tone: 'accent' },
  monitoring: { label: 'In Progress', tone: 'accent' },
  mitigated: { label: 'Pending', tone: 'success' },
  closed: { label: 'Closed', tone: 'neutral' },
}

export function statusMeta(status?: string | null): StatusMeta {
  const s = (status ?? '').toLowerCase().trim()
  if (s in TABLE) return TABLE[s]
  const label = s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unknown'
  return { label, tone: 'neutral' }
}
