import { LogOut, ShieldAlert } from 'lucide-react'
import { navSectionsForRole, type View } from '../../lib/nav'
import type { Role } from '../../lib/types'

export function Sidebar({
  view,
  onNavigate,
  email,
  role,
  onSignOut,
}: {
  view: View
  onNavigate: (v: View) => void
  email: string | null
  role: Role | null
  onSignOut: () => void
}) {
  const initials = (email ?? '?').slice(0, 2).toUpperCase()
  const sections = navSectionsForRole(role)
  return (
    <aside className="hidden w-64 shrink-0 flex-col bg-rail text-rail-text md:flex">
      {/* Brand */}
      <div className="flex items-center gap-3 px-5 py-5">
        <span
          className="flex h-10 w-10 items-center justify-center rounded-xl text-white shadow-rail"
          style={{ background: 'linear-gradient(135deg, var(--accent), var(--purple))' }}
        >
          <ShieldAlert size={20} strokeWidth={2.2} />
        </span>
        <div className="leading-tight">
          <div className="font-display text-lg font-extrabold tracking-tight text-white">IIM</div>
          <div className="text-[11px] font-medium text-rail-dim">Incident Intelligence</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-1">
        {sections.map((section) => (
          <div key={section.heading} className="mb-5">
            <div className="px-3 pb-2 text-[10px] font-bold uppercase tracking-[0.16em] text-rail-dim">
              {section.heading}
            </div>
            <div className="space-y-1">
              {section.items.map((item) => {
                const Icon = item.icon
                const active = view === item.view
                return (
                  <button
                    key={item.view}
                    onClick={() => onNavigate(item.view)}
                    aria-current={active ? 'page' : undefined}
                    className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                      active
                        ? 'bg-[var(--rail-active-bg)] text-white'
                        : 'text-rail-text hover:bg-[var(--rail-hover)] hover:text-white'
                    }`}
                  >
                    {active && (
                      <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-accent" />
                    )}
                    <Icon size={18} strokeWidth={2.1} className={active ? '' : 'opacity-80'} />
                    {item.label}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-rail-hair px-3 py-3">
        <div className="flex items-center gap-3 rounded-xl px-3 py-2">
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-xs font-bold text-white"
            style={{ background: 'linear-gradient(135deg, var(--accent), var(--purple))' }}
          >
            {initials}
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-sm font-semibold text-white">{email}</div>
            <div className="text-[11px] font-medium capitalize text-rail-dim">{role ?? 'Signed in'}</div>
          </div>
        </div>
        <button
          onClick={onSignOut}
          className="mt-1 flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold text-rail-text transition hover:bg-[var(--rail-hover)] hover:text-white"
        >
          <LogOut size={18} strokeWidth={2.1} /> Sign out
        </button>
        <div className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-rail-dim">
          Local test build · v0.1
        </div>
      </div>
    </aside>
  )
}
