import { BellOff, Repeat2 } from 'lucide-react'
import type { NoisyAlarm } from '../lib/types'
import { Card, CardHeader } from './ui/Card'
import { Skeleton } from './ui/Skeleton'

/**
 * What is firing over and over, grouped by fingerprint (the same problem re-firing shares one).
 *
 * With a few incidents a plain reverse-chronological feed is fine; with hundreds, the question
 * stops being "what fired last" and becomes "what is spamming us" — which is the thing you can
 * actually act on, by fixing the alarm's threshold or the underlying flap.
 */
export function NoisyAlarms({
  alarms,
  windowHours = 24,
  loading,
}: {
  alarms: NoisyAlarm[]
  windowHours?: number
  loading: boolean
}) {
  const max = Math.max(1, ...alarms.map((a) => a.count))
  return (
    <Card className="flex h-full min-w-0 flex-col p-5 md:p-6">
      <CardHeader title={`Noisiest · last ${windowHours}h`} />
      <div className="mt-4 flex min-h-0 flex-1 flex-col">
        {loading ? (
          <div className="space-y-3">
            {[0, 1, 2].map((k) => (
              <Skeleton key={k} className="h-10" />
            ))}
          </div>
        ) : alarms.length === 0 ? (
          // Centred in whatever height the row gives it. Given a hero treatment this became the
          // tallest block on the dashboard; pinned to the top it left the card looking half-built.
          // Quiet news, sitting calmly in the middle of its own card, reads as neither.
          <div className="flex flex-1 flex-col items-center justify-center gap-2 py-6 text-center">
            <BellOff size={18} className="text-muted" />
            <p className="max-w-[24ch] text-xs leading-relaxed text-ink-2">
              Nothing repeating — no alarm fired more than once in the last {windowHours}h.
            </p>
          </div>
        ) : (
          <ul className="space-y-3">
            {alarms.map((a) => (
              <li key={`${a.service}-${a.fingerprint}`}>
                <div className="flex items-center gap-3">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-ink">{a.label}</span>
                    <span className="text-xs text-muted">{a.service}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2 py-1 text-xs font-bold tabular-nums text-ink-2">
                    <Repeat2 size={13} />
                    {a.count}×
                  </span>
                </div>
                {/* A bar rather than just the number: relative volume is the point — one alarm at
                    40× next to one at 3× is a different situation than two at 20×. */}
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(a.count / max) * 100}%`,
                      background: 'var(--sev-medium)',
                    }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}
