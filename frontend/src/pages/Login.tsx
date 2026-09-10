import { useState } from 'react'
import { Eye, EyeOff, ShieldAlert } from 'lucide-react'
import { errText } from '../lib/api'
import { Button } from '../components/ui/Button'

const inputClass =
  'w-full rounded-xl border border-transparent bg-surface-2 px-4 py-3 text-sm text-ink ' +
  'placeholder:text-muted outline-none transition focus:border-accent focus:bg-surface ' +
  'focus:ring-2 focus:ring-[var(--accent-weak)]'

/** IIM logo chip — the gradient shield used across the app's brand marks. */
function LogoChip({ size = 40 }: { size?: number }) {
  return (
    <span
      className="flex items-center justify-center rounded-xl text-white shadow-rail"
      style={{
        width: size,
        height: size,
        background: 'linear-gradient(135deg, var(--accent), var(--purple))',
      }}
    >
      <ShieldAlert size={size * 0.5} strokeWidth={2.2} />
    </span>
  )
}

/**
 * The sign-in screen — a full-bleed, macOS-style split. Left: a dark-indigo panel carrying the
 * IIM identity and its thesis. Right: the sign-in form, held to a readable column. Both halves fill
 * the viewport; below `md` the brand panel drops away and the form stands alone. `onSignIn` calls
 * the real `POST /api/auth/login` (see lib/auth.ts) and rejects on bad credentials.
 */
export function Login({
  onSignIn,
}: {
  onSignIn: (email: string, password: string, remember: boolean) => Promise<void>
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [show, setShow] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSignIn(email, password, remember)
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-full md:grid-cols-2">
      {/* Brand panel — carries the IIM identity and product thesis. */}
      <aside
        className="relative hidden flex-col justify-between overflow-hidden p-10 text-white md:flex lg:p-14"
        style={{
          background:
            'linear-gradient(150deg, var(--accent-strong), var(--accent) 52%, var(--purple))',
        }}
      >
        {/* Ambient glow orbs for macOS-style depth. */}
        <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/15 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-20 -left-10 h-56 w-56 rounded-full bg-white/10 blur-3xl" />

        {/* Top: brand lockup, nudged down off the corner. */}
        <div className="relative mt-8 flex items-center gap-2.5">
          <LogoChip />
          <div className="leading-tight">
            <div className="font-display text-xl font-extrabold tracking-tight">IIM</div>
            <div className="text-xs font-medium text-white/70">
              Intelligent Incident Management
            </div>
          </div>
        </div>

        {/* Centre: the product thesis — the panel's focal point. */}
        <div className="relative">
          <h2 className="font-display text-4xl font-extrabold leading-[1.1] tracking-tight lg:text-5xl">
            What&rsquo;s on fire,
            <br />
            and why.
          </h2>
          <p className="mt-5 max-w-sm text-base leading-relaxed text-white/75">
            AI root-cause triage, grounded in your own runbooks and postmortems — the full picture
            in under a minute.
          </p>
        </div>

        <p className="relative text-xs font-medium text-white/55">Local incident console · v0.1</p>
      </aside>

      {/* Sign-in form — held to a readable column, centred in the panel. */}
      <div className="flex items-center justify-center bg-surface px-6 py-12 sm:px-10">
        <div className="animate-in w-full max-w-lg">
          {/* Compact brand for small screens (the brand panel is hidden below md). */}
          <div className="mb-8 flex items-center gap-3 md:hidden">
            <LogoChip size={36} />
            <div className="font-display text-lg font-extrabold tracking-tight text-ink">IIM</div>
          </div>

          <h1 className="font-display text-2xl font-extrabold tracking-tight text-ink">
            Welcome back
          </h1>
          <p className="mt-1 text-sm text-ink-2">Sign in to your incident console.</p>

          <form className="mt-7 space-y-4" onSubmit={submit}>
            <div className="space-y-1">
              <label htmlFor="email" className="text-xs font-medium text-ink-2">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@emesoft.net"
                className={inputClass}
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="password" className="text-xs font-medium text-ink-2">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                  className={`${inputClass} pr-11`}
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  aria-label={show ? 'Hide password' : 'Show password'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-muted transition hover:text-ink-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {show ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between">
              <label className="flex cursor-pointer select-none items-center gap-2.5">
                <button
                  type="button"
                  role="switch"
                  aria-checked={remember}
                  onClick={() => setRemember((r) => !r)}
                  className={`relative h-5 w-9 shrink-0 rounded-full transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface ${
                    remember ? 'bg-accent' : 'border border-hair bg-surface-2'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
                      remember ? 'left-[18px]' : 'left-0.5'
                    }`}
                  />
                </button>
                <span className="text-sm text-ink-2">Remember me</span>
              </label>
            </div>

            {error && <p className="text-sm text-sev-critical">{error}</p>}

            <Button type="submit" className="w-full py-3" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </div>
      </div>
    </div>
  )
}
