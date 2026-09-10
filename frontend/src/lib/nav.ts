import {
  AlertTriangle,
  BookOpen,
  ClipboardList,
  LayoutDashboard,
  Settings,
  Users,
  type LucideIcon,
} from 'lucide-react'
import type { Role } from './types'

export type View = 'overview' | 'incidents' | 'knowledge' | 'reports' | 'settings' | 'users'

export interface NavItem {
  view: View
  label: string
  icon: LucideIcon
  /** Roles that can see this nav entry. Omit to show it to everyone signed in. */
  roles?: Role[]
}

export interface NavSection {
  heading: string
  items: NavItem[]
}

/**
 * Sidebar structure. Every item is wired to a real view; `roles` restricts an item to specific
 * roles (e.g. Settings/Users are admin-only) — see `navSectionsForRole`.
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    heading: 'Overview',
    items: [{ view: 'overview', label: 'Dashboard', icon: LayoutDashboard }],
  },
  {
    heading: 'Operations',
    items: [
      { view: 'incidents', label: 'Incidents', icon: AlertTriangle },
      { view: 'knowledge', label: 'Knowledge Base', icon: BookOpen },
      { view: 'reports', label: 'Reports', icon: ClipboardList },
    ],
  },
  {
    heading: 'System',
    items: [
      { view: 'settings', label: 'Settings', icon: Settings, roles: ['admin'] },
      { view: 'users', label: 'Users', icon: Users, roles: ['admin'] },
    ],
  },
]

/** Filters `NAV_SECTIONS` down to the items a given role may see, dropping empty sections. */
export function navSectionsForRole(role: Role | null): NavSection[] {
  return NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((item) => !item.roles || (role !== null && item.roles.includes(role))),
  })).filter((section) => section.items.length > 0)
}

export const VIEW_META: Record<View, { title: string; subtitle: string }> = {
  overview: {
    title: 'Dashboard',
    subtitle: 'Incident posture, knowledge base, and live backend health',
  },
  incidents: {
    title: 'Incidents',
    subtitle: 'Ingest, triage, and inspect AI analysis with cited evidence',
  },
  knowledge: {
    title: 'Knowledge Base',
    subtitle: 'Documents indexed for retrieval-augmented analysis',
  },
  reports: {
    title: 'Reports',
    subtitle: "Roll up a day's incidents into a Slack-postable digest",
  },
  settings: {
    title: 'Settings',
    subtitle: 'AWS connections used to poll CloudWatch alarms into incidents',
  },
  users: {
    title: 'Users',
    subtitle: 'Manage accounts and groups for this console',
  },
}
