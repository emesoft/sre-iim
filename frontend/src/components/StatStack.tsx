import type { LucideIcon } from 'lucide-react'
import { Badge, type BadgeTone } from './ui/Badge'
import { Skeleton } from './ui/Skeleton'

export type Stat = {
  label: string
  value: string | number
  icon: LucideIcon
  accent: string
  badge?: { text: string; tone?: BadgeTone }
}

/**
 * The supporting numbers, as compact rows in one card rather than a row of equal-sized tiles.
 * These are context, not calls to action — stacking them keeps them legible while leaving the
 * dashboard's prime space to the one card that does demand a decision (AttentionCard).
 */
export function StatStack({ items, loading }: { items: Stat[]; loading: boolean }) {
  if (loading) {
    return <Skeleton className="h-[210px] rounded-2xl" />
  }
  return (
    <div className="divide-y divide-hair rounded-2xl border border-hair bg-surface px-5 shadow-card">
      {items.map(({ label, value, icon: Icon, accent, badge }) => (
        <div key={label} className="flex items-center gap-3 py-4">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
            style={{ background: `color-mix(in srgb, ${accent} 15%, transparent)`, color: accent }}
          >
            <Icon size={17} strokeWidth={2.2} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-ink">{label}</div>
            {badge && (
              <div className="mt-0.5">
                <Badge tone={badge.tone ?? 'neutral'}>{badge.text}</Badge>
              </div>
            )}
          </div>
          <span className="font-display text-2xl font-extrabold tabular-nums leading-none text-ink">
            {value}
          </span>
        </div>
      ))}
    </div>
  )
}
