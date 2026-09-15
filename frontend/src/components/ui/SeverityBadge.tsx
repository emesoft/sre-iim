import { severityMeta } from '../../lib/severity'

/**
 * Severity pill: a status-color dot + text label. The label is always present,
 * so severity is never communicated by color alone (dataviz status rule).
 */
export function SeverityBadge({
  severity,
  status,
  size = 'sm',
}: {
  severity?: string | null
  /** Disambiguates a missing severity: "Pending" (still open) vs "No severity" (closed without
   * ever being analyzed) — see severityMeta. */
  status?: string | null
  size?: 'sm' | 'xs'
}) {
  const m = severityMeta(severity, status)
  const pad = size === 'xs' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-0.5 text-xs'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${pad}`}
      style={{ background: `color-mix(in srgb, ${m.color} 15%, transparent)`, color: m.color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: m.color }} />
      {m.label}
    </span>
  )
}
