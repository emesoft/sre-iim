// Small presentation helpers shared across the dashboard.

/** Compact relative time, e.g. "just now", "12m ago", "3h ago", "Feb 4". */
export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const secs = Math.floor((Date.now() - then) / 1000)
  if (secs < 45) return 'just now'
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** A short, uppercase reference derived from an opaque id (for display only). */
export function shortRef(id: string): string {
  return id.replace(/[^a-z0-9]/gi, '').slice(0, 6).toUpperCase() || '------'
}

/** Human incident label, e.g. "INC-4F9A2C". */
export function incidentRef(id: string): string {
  return `INC-${shortRef(id)}`
}

