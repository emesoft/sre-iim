/**
 * Sparky — IIM's mascot: a firefighter robot, for a product whose whole job is "what's on fire,
 * and why".
 *
 * Hand-drawn flat SVG on a 48×48 grid, deliberately built from a handful of primitives so the
 * silhouette (helmet dome + wide brim + visor) still reads at 20px in the nav rail as well as at
 * 120px in an empty state. It is drawn in the theme's accent tokens rather than fixed hex, so the
 * one red mark works on the light dashboard, the dark dashboard and the near-black sign-in panel
 * without a second asset.
 *
 * `mood` is meant to be driven by real state, not decoration:
 * - `idle`   — neutral, the default (nav rail, sign-in).
 * - `alert`  — beacon lit: something urgent is waiting.
 * - `happy`  — eyes closed in a smile: nothing on fire.
 */
export function Sparky({
  size = 32,
  mood = 'idle',
  className = '',
}: {
  size?: number
  mood?: 'idle' | 'alert' | 'happy'
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      role="img"
      aria-label={
        mood === 'alert'
          ? 'Sparky, alert'
          : mood === 'happy'
            ? 'Sparky, all clear'
            : 'Sparky, the IIM mascot'
      }
    >
      {/* Beacon — only lit when something needs a human. */}
      {mood === 'alert' && (
        <>
          <circle className="sparky-beacon" cx={24} cy={13} r={5} fill="var(--sev-medium)" />
          <circle cx={24} cy={13} r={2.4} fill="var(--sev-medium)" />
        </>
      )}

      {/* Ear units. */}
      <rect x={7.6} y={29.2} width={3.8} height={5.6} rx={1.9} fill="var(--accent-strong)" />
      <rect x={36.6} y={29.2} width={3.8} height={5.6} rx={1.9} fill="var(--accent-strong)" />

      {/* Head. */}
      <rect x={11} y={16} width={26} height={24} rx={9} fill="var(--accent)" />

      {/* Helmet dome — traces exactly the head's own rounded top edge. */}
      <path d="M11 25 A9 9 0 0 1 20 16 H28 A9 9 0 0 1 37 25 Z" fill="var(--accent-strong)" />

      {/* Helmet shield badge, tucked behind the brim. */}
      <path
        d="M21 17.4h6v3.4c0 2.3-1.7 3.6-3 4.1-1.3-.5-3-1.8-3-4.1z"
        fill="#ffffff"
        fillOpacity={0.92}
      />

      {/* Brim — the widest thing in the silhouette, which is what makes it read as a helmet. */}
      <rect x={6.5} y={23.4} width={35} height={4.6} rx={2.3} fill="var(--accent-strong)" />

      {/* Visor. */}
      <rect x={14.5} y={29} width={19} height={8.6} rx={4.3} fill="#101728" />

      {mood === 'happy' ? (
        <g stroke="#f8fafc" strokeWidth={1.7} strokeLinecap="round" fill="none">
          <path d="M18.4 34.2q1.6-2.2 3.2 0" />
          <path d="M26.4 34.2q1.6-2.2 3.2 0" />
        </g>
      ) : (
        <g fill={mood === 'alert' ? 'var(--sev-medium)' : '#f8fafc'}>
          <circle cx={20} cy={33.3} r={1.75} />
          <circle cx={28} cy={33.3} r={1.75} />
        </g>
      )}
    </svg>
  )
}
