import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Sparky } from '../Sparky'

/** An empty screen framed as an invitation to act, not a dead end. `mascot` swaps the icon chip
 * for Sparky — worth it on the big first-run screens, too much on a small inline empty slot. */
export function EmptyState({
  icon: Icon,
  title,
  hint,
  action,
  mascot = false,
  className = '',
}: {
  icon: LucideIcon
  title: string
  hint?: string
  action?: ReactNode
  mascot?: boolean
  className?: string
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-2xl border border-dashed border-hair px-6 py-14 text-center ${className}`}
    >
      {mascot ? (
        <Sparky size={64} mood="happy" className="mb-4" />
      ) : (
        <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-2 text-muted">
          <Icon size={26} strokeWidth={1.75} />
        </span>
      )}
      <h3 className="font-display text-base font-bold text-ink">{title}</h3>
      {hint && <p className="mt-1.5 max-w-sm text-sm text-ink-2">{hint}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
