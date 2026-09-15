import { LogOut, ShieldQuestion } from 'lucide-react'
import { Button } from '../components/ui/Button'
import { Sparky } from '../components/Sparky'

/**
 * What a signed-in account sees before an admin has put it in a group — the Guest state.
 *
 * Shown instead of the console — not as an error, because nothing is broken: the account works,
 * it just has nothing to look at yet. Every project-scoped endpoint would return empty for this
 * user anyway, so without this screen the app would look convincingly like a system with no
 * incidents, no documents and no history, which is a far more alarming thing to hand someone on
 * their first login than a sentence explaining what to ask for.
 */
export function NoAccess({
  username,
  onSignOut,
}: {
  username: string
  onSignOut: () => void
}) {
  return (
    <div className="plane-aurora flex h-full items-center justify-center px-6">
      <div className="animate-in w-full max-w-lg rounded-2xl border border-hair bg-surface p-8 text-center shadow-card">
        <Sparky size={72} mood="idle" className="mx-auto" />

        <h1 className="mt-5 font-display text-2xl font-extrabold tracking-tight text-ink">
          You&rsquo;re signed in, {username}
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-2">
          Your account isn&rsquo;t in a group yet, so there&rsquo;s nothing for it to show. Ask an
          administrator to put you in the group that covers your projects — incidents, runbooks and
          reports appear here as soon as they do.
        </p>

        <div className="mt-6 flex items-center justify-center gap-2.5 rounded-xl bg-surface-2 px-4 py-3 text-left">
          <ShieldQuestion size={18} className="shrink-0 text-muted" />
          <p className="text-xs leading-relaxed text-ink-2">
            A group decides both what you can do and which projects you see. An admin assigns one
            on the <span className="font-semibold text-ink">Users</span> page under Settings.
          </p>
        </div>

        <div className="mt-6">
          <Button variant="ghost" onClick={onSignOut}>
            <LogOut size={15} /> Sign out
          </Button>
        </div>
      </div>
    </div>
  )
}
