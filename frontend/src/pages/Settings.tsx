import { useCallback, useEffect, useState } from 'react'
import { Boxes, Cpu, ShieldOff, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import { api } from '../lib/api'
import type { Project, EffectiveRole } from '../lib/types'
import { IntegrationsPanel } from '../features/settings/IntegrationsPanel'
import { LlmProfilesPanel } from '../features/settings/LlmProfilesPanel'
import { LlmUsageCard } from '../features/settings/LlmUsageCard'
import { EmptyState } from '../components/ui/EmptyState'

type Section = 'integrations' | 'ai' | 'advanced'

const SECTIONS: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: 'integrations', label: 'Integrations', icon: Boxes },
  { id: 'ai', label: 'AI & usage', icon: Cpu },
  { id: 'advanced', label: 'Advanced', icon: SlidersHorizontal },
]

export function Settings({ role }: { role: EffectiveRole | null }) {
  if (role !== 'admin') {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <EmptyState
          icon={ShieldOff}
          title="Insufficient permissions"
          hint="Settings is admin-only. Ask an admin to change your group if you need access."
        />
      </div>
    )
  }
  return <SettingsContent />
}

/**
 * Settings, split by concern rather than stacked on one scrolling page.
 *
 * It used to be a single column mixing the Claude token, token usage, poll cadence, the project
 * registry and every connection of every project — fine with two projects, unreadable with ten.
 * The sub-nav keeps each concern one click away and lets the integrations view use the full width
 * for its own master/detail.
 */
function SettingsContent() {
  const [section, setSection] = useState<Section>('integrations')
  const [projects, setProjects] = useState<Project[]>([])

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await api.get<Project[]>('/api/projects'))
    } catch {
      setProjects([]) // non-critical — the panel shows its own empty state
    }
  }, [])

  useEffect(() => {
    loadProjects()
  }, [loadProjects])

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in grid grid-cols-1 gap-6 lg:grid-cols-[180px_1fr]">
        <nav className="flex flex-row gap-1 lg:flex-col">
          {SECTIONS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setSection(id)}
              aria-current={section === id ? 'page' : undefined}
              className={`flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                section === id
                  ? 'bg-surface text-ink shadow-card'
                  : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
              }`}
            >
              <Icon size={16} strokeWidth={2.1} />
              {label}
            </button>
          ))}
        </nav>

        <div className="min-w-0">
          {section === 'integrations' && (
            <IntegrationsPanel projects={projects} onProjectsChanged={loadProjects} />
          )}
          {section === 'ai' && (
            <div className="flex flex-col gap-6">
              <LlmProfilesPanel projects={projects} />
              <LlmUsageCard />
            </div>
          )}
          {section === 'advanced' && (
            <div className="rounded-2xl border border-hair bg-surface p-5 text-sm text-ink-2 shadow-card">
              <h3 className="font-display text-base font-bold text-ink">Advanced</h3>
              <p className="mt-2 leading-relaxed">
                Poll cadence and automatic-triage limits are environment settings
                (<span className="font-mono text-xs text-ink">ALARM_POLL_INTERVAL_MINUTES</span>,{' '}
                <span className="font-mono text-xs text-ink">AUTO_ANALYZE_PRIORITIES</span>,{' '}
                <span className="font-mono text-xs text-ink">AUTO_ANALYZE_MAX_PER_RUN</span>) — they
                apply to the whole deployment, so they live in <span className="font-mono text-xs text-ink">.env</span>{' '}
                rather than here. Per-project auto-triage has its own switch under Integrations.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
