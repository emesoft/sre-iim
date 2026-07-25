import { PlugZap, RefreshCw } from 'lucide-react'
import { Button } from './Button'

/**
 * The designed failure state. Explains what went wrong and exactly how to fix
 * it (start the backend), shows the raw detail, and offers a retry — errors
 * give direction, they don't just apologize.
 */
export function ErrorState({
  detail,
  onRetry,
  className = '',
}: {
  detail: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-2xl border border-hair bg-surface px-6 py-14 text-center shadow-card ${className}`}
    >
      <span
        className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl"
        style={{
          background: 'color-mix(in srgb, var(--sev-critical) 14%, transparent)',
          color: 'var(--sev-critical)',
        }}
      >
        <PlugZap size={26} />
      </span>
      <h3 className="font-display text-base font-bold text-ink">Can’t reach the backend</h3>
      <p className="mt-1.5 max-w-md text-sm leading-relaxed text-ink-2">
        The API on <span className="font-mono text-ink">:8000</span> didn’t respond. Start it with{' '}
        <span className="font-mono text-ink">docker compose up</span> from the repo root, then
        retry.
      </p>
      <code className="mt-4 max-w-md truncate rounded-lg bg-surface-2 px-3 py-1.5 font-mono text-xs text-ink-2">
        {detail}
      </code>
      {onRetry && (
        <div className="mt-5">
          <Button variant="ghost" onClick={onRetry}>
            <RefreshCw size={15} /> Retry
          </Button>
        </div>
      )}
    </div>
  )
}
