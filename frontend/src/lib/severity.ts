// Severity → status palette (fixed, pre-validated in the dataviz reference).
// Every rendering pairs the color with a text label, so meaning is never color-alone.

export type SeverityKey = 'critical' | 'high' | 'medium' | 'low' | 'pending' | 'closed' | 'unknown'

export interface SeverityMeta {
  key: SeverityKey
  label: string
  /** CSS custom-property reference for the status color. */
  color: string
  /** True for severities that should draw the eye (dashboard "needs attention"). */
  urgent: boolean
}

const TABLE: Record<Exclude<SeverityKey, 'unknown' | 'pending' | 'closed'>, SeverityMeta> = {
  critical: { key: 'critical', label: 'Critical', color: 'var(--sev-critical)', urgent: true },
  high: { key: 'high', label: 'High', color: 'var(--sev-high)', urgent: true },
  medium: { key: 'medium', label: 'Medium', color: 'var(--sev-medium)', urgent: false },
  low: { key: 'low', label: 'Low', color: 'var(--sev-low)', urgent: false },
}

// The analysis prompt used to emit "warning"/"info" instead of the medium/low tier before this
// mismatch was fixed (backend/app/domain/incidents/prompts.py) — incidents analyzed before that
// fix still have these values stored, so they're mapped here rather than left as "Unknown"
// forever or requiring every old incident to be re-analyzed.
const LEGACY_ALIASES: Record<string, Exclude<SeverityKey, 'unknown' | 'pending' | 'closed'>> = {
  warning: 'medium',
  info: 'low',
}

/**
 * `status` disambiguates a missing severity: on an open incident it means "not analyzed yet"
 * ("Pending", info-blue — normal, not an error); on a resolved/ticketed one it means "closed
 * without ever running an analysis" ("No severity", neutral gray) — an incident can now be
 * resolved with no analysis at all (see ResolveIncident), and "Pending" on something already
 * closed reads as if it's still waiting on something, which it isn't.
 */
export function severityMeta(severity?: string | null, status?: string | null): SeverityMeta {
  if (!severity) {
    if (status === 'resolved' || status === 'ticketed') {
      return { key: 'closed', label: 'No severity', color: 'var(--muted)', urgent: false }
    }
    return { key: 'pending', label: 'Pending', color: 'var(--info)', urgent: false }
  }
  const s = severity.toLowerCase()
  if (s in TABLE) return TABLE[s as keyof typeof TABLE]
  if (s in LEGACY_ALIASES) return TABLE[LEGACY_ALIASES[s]]
  return { key: 'unknown', label: severity, color: 'var(--muted)', urgent: false }
}

export const SEVERITY_ORDER: Exclude<SeverityKey, 'unknown' | 'pending' | 'closed'>[] = [
  'critical',
  'high',
  'medium',
  'low',
]
