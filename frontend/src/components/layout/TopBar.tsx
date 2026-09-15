import { Bell, Search } from 'lucide-react'
import { HealthPill } from '../HealthPill'
import { ThemeToggle } from '../ThemeToggle'
import type { Theme } from '../../lib/theme'
import type { EffectiveRole } from '../../lib/types'

export function TopBar({
  query,
  onQueryChange,
  alertCount,
  onBellClick,
  theme,
  onToggleTheme,
  username,
  email,
  role,
}: {
  query: string
  onQueryChange: (v: string) => void
  alertCount: number
  onBellClick: () => void
  theme: Theme
  onToggleTheme: () => void
  username: string | null
  email: string | null | undefined
  role: EffectiveRole | null
}) {
  const initials = (username ?? email ?? '?').slice(0, 2).toUpperCase()
  return (
    <header className="z-20 flex items-center gap-4 border-b border-hair bg-surface/85 px-4 py-3 backdrop-blur md:px-8">
      <div className="relative w-full max-w-xl">
        <Search
          size={16}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
        />
        <input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search incidents, documents, fingerprints…"
          aria-label="Search"
          className="w-full rounded-xl border border-hair bg-plane py-2 pl-9 pr-3 text-sm text-ink outline-none transition placeholder:text-muted focus:border-accent focus:ring-2 focus:ring-[var(--accent-weak)]"
        />
      </div>

      <div className="ml-auto flex items-center gap-2.5">
        <div className="hidden sm:block">
          <HealthPill />
        </div>

        <button
          onClick={onBellClick}
          aria-label={alertCount > 0 ? `${alertCount} incidents need attention` : 'Alerts'}
          title={alertCount > 0 ? `${alertCount} need attention` : 'No incidents need attention'}
          className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-hair bg-surface text-ink-2 transition hover:bg-surface-2 hover:text-ink"
        >
          <Bell size={17} />
          {alertCount > 0 && (
            <span className="absolute -right-1 -top-1 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-sev-critical px-1 text-[10px] font-bold leading-none text-white">
              {alertCount > 9 ? '9+' : alertCount}
            </span>
          )}
        </button>

        <ThemeToggle theme={theme} onToggle={onToggleTheme} />

        <div className="ml-1 flex items-center gap-2.5 rounded-xl border border-hair bg-surface py-1 pl-1 pr-3">
          <span
            className="flex h-7 w-7 items-center justify-center rounded-lg text-xs font-bold text-white"
            style={{ background: 'linear-gradient(135deg, var(--accent), var(--purple))' }}
          >
            {initials}
          </span>
          <div className="hidden leading-tight lg:block">
            <div className="text-xs font-bold text-ink">{username ?? 'Signed in'}</div>
            <div className="text-[10px] capitalize text-muted">{role ?? 'unknown role'}</div>
          </div>
        </div>
      </div>
    </header>
  )
}
