import { useState } from 'react'
import { Plus } from 'lucide-react'
import { useAuth } from './lib/auth'
import { useTheme, type Theme } from './lib/theme'
import { useDashboard } from './lib/useDashboard'
import { VIEW_META, type View } from './lib/nav'
import { severityMeta } from './lib/severity'
import type { Role, UserOut } from './lib/types'
import { Sidebar } from './components/layout/Sidebar'
import { TopBar } from './components/layout/TopBar'
import { PageHeader } from './components/layout/PageHeader'
import { Button } from './components/ui/Button'
import { Login } from './pages/Login'
import { Overview } from './pages/Overview'
import { Incidents } from './pages/Incidents'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Reports } from './pages/Reports'
import { Settings } from './pages/Settings'
import { UsersPage } from './features/users/UsersPage'
import { NewIncidentModal } from './features/incidents/NewIncidentModal'
import { NewDocumentModal } from './features/documents/NewDocumentModal'

export default function App() {
  // useTheme runs on both screens so the login page respects light/dark too.
  const { theme, toggle } = useTheme()
  const { authed, user, role, signIn, signOut } = useAuth()

  if (!authed || !user) return <Login onSignIn={signIn} />
  return (
    <Dashboard theme={theme} onToggleTheme={toggle} user={user} role={role} onSignOut={signOut} />
  )
}

function Dashboard({
  theme,
  onToggleTheme,
  user,
  role,
  onSignOut,
}: {
  theme: Theme
  onToggleTheme: () => void
  user: UserOut
  role: Role | null
  onSignOut: () => void
}) {
  const [view, setView] = useState<View>('overview')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dataVersion, setDataVersion] = useState(0)
  const [query, setQuery] = useState('')
  const [showIncident, setShowIncident] = useState(false)
  const [showDoc, setShowDoc] = useState(false)

  const data = useDashboard(dataVersion)
  const refresh = () => setDataVersion((v) => v + 1)
  const alertCount = data.incidents.filter((i) => severityMeta(i.severity).urgent).length

  // Consultants are read-only everywhere: they can view incidents/documents/reports but cannot
  // create, resolve, ticket, chat, or ingest anything (backend 403s these routes for their role).
  const canMutate = role === 'admin' || role === 'sre'

  const openIncident = (id: string) => {
    setSelectedId(id)
    setView('incidents')
  }

  const meta = VIEW_META[view]
  const action =
    view === 'knowledge' && canMutate ? (
      <Button onClick={() => setShowDoc(true)}>
        <Plus size={16} /> New document
      </Button>
    ) : view === 'incidents' && canMutate ? (
      <Button onClick={() => setShowIncident(true)}>
        <Plus size={16} /> New incident
      </Button>
    ) : undefined

  return (
    <div className="flex h-full">
      <Sidebar view={view} onNavigate={setView} email={user.email} role={role} onSignOut={onSignOut} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          query={query}
          onQueryChange={setQuery}
          alertCount={alertCount}
          onBellClick={() => setView('incidents')}
          theme={theme}
          onToggleTheme={onToggleTheme}
        />
        <main className="plane-aurora flex min-h-0 flex-1 flex-col">
          {view !== 'users' && <PageHeader title={meta.title} subtitle={meta.subtitle} action={action} />}
          <div className="min-h-0 flex-1">
            {view === 'overview' && (
              <Overview
                data={data}
                query={query}
                onOpenIncident={openIncident}
                onViewAll={() => setView('incidents')}
                onRetry={refresh}
              />
            )}
            {view === 'incidents' && (
              <Incidents
                data={data}
                query={query}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onRetry={refresh}
                canMutate={canMutate}
              />
            )}
            {view === 'knowledge' && (
              <KnowledgeBase
                data={data}
                query={query}
                onRetry={refresh}
                onNew={() => setShowDoc(true)}
                canMutate={canMutate}
              />
            )}
            {view === 'reports' && <Reports incidents={data.incidents} />}
            {view === 'settings' && <Settings role={role} />}
            {view === 'users' &&
              (role === 'admin' ? (
                <UsersPage currentUserId={user.id} />
              ) : (
                <Settings role={role} />
              ))}
          </div>
        </main>
      </div>

      {canMutate && (
        <>
          <NewIncidentModal
            open={showIncident}
            onClose={() => setShowIncident(false)}
            onCreated={(id) => {
              setSelectedId(id)
              setView('incidents')
              refresh()
            }}
          />
          <NewDocumentModal open={showDoc} onClose={() => setShowDoc(false)} onCreated={refresh} />
        </>
      )}
    </div>
  )
}
