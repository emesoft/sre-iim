import { useEffect, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { errText } from '../lib/api'
import { Button } from '../components/ui/Button'
import { BrandMark } from '../components/ui/BrandMark'
import { OrchestrationMap } from '../components/OrchestrationMap'
import { loadEntraConfig } from '../lib/entra'

const inputClass =
  'w-full rounded-xl border border-[var(--login-hair)] bg-white/5 px-4 py-3 text-sm text-[var(--login-text)] ' +
  'placeholder:text-[var(--login-text-dim)] outline-none transition focus:border-[var(--login-accent)] ' +
  'focus:bg-white/[0.07] focus:ring-2 focus:ring-[var(--login-accent-weak)]'

/** Microsoft's brand mark — four squares, drawn rather than loaded so the sign-in screen keeps
 * working with no network beyond our own origin. */
function MicrosoftLogo({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 21 21" aria-hidden>
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}

/**
 * The sign-in screen — a fixed dark treatment independent of the app's light/dark toggle
 * (Emesoft red on near-black, matching the company mark), split macOS-style. Left: the product
 * diagram; right: the sign-in card.
 *
 * Two ways in, both ending in the same application JWT: `onSignIn` posts username/password to
 * `/api/auth/login`, and `onSignInWithMicrosoft` runs the Entra popup and exchanges its ID token
 * at `/api/auth/entra`. The Microsoft button only appears when the server reports an app
 * registration is configured.
 */
export function Login({
  onSignIn,
  onSignInWithMicrosoft,
}: {
  onSignIn: (username: string, password: string, remember: boolean) => Promise<void>
  onSignInWithMicrosoft: (remember: boolean) => Promise<void>
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [show, setShow] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Only offered when the server actually has an Entra app registration — a button that always
  // fails is worse than no button.
  const [ssoEnabled, setSsoEnabled] = useState(false)

  useEffect(() => {
    let alive = true
    loadEntraConfig().then((c) => alive && setSsoEnabled(c.enabled))
    return () => {
      alive = false
    }
  }, [])

  const signInWithMicrosoft = async () => {
    setError(null)
    setSubmitting(true)
    try {
      await onSignInWithMicrosoft(remember)
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSignIn(username, password, remember)
    } catch (e) {
      setError(errText(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid min-h-full md:grid-cols-2" style={{ background: 'var(--login-bg)' }}>
      {/* Brand panel — the product thesis, then the map of what IIM takes in and puts out. */}
      <aside
        className="relative hidden flex-col justify-between overflow-hidden p-10 text-[var(--login-text)] md:flex lg:p-12"
        style={{
          background:
            'radial-gradient(60rem 30rem at 15% 0%, color-mix(in srgb, var(--login-accent) 16%, transparent), transparent 65%), var(--login-panel)',
        }}
      >
        <div className="relative">
          <BrandMark variant="full" height={40} />
        </div>

        <div className="relative">
          <h2 className="font-display text-4xl font-extrabold leading-[1.05] tracking-tight">
            What&rsquo;s on fire,
            <br />
            and why.
          </h2>
          <p className="mt-4 max-w-sm text-[15px] leading-relaxed text-[var(--login-text-dim)]">
            AI root-cause triage for IIM, grounded in your own runbooks and postmortems — the full
            picture in under a minute.
          </p>

          <div className="mx-auto mt-6 hidden w-full max-w-[520px] lg:block">
            <OrchestrationMap />
          </div>
        </div>

        <p className="relative text-xs font-medium text-[var(--login-text-dim)]">
          Made by Cloud team · v0.1
        </p>
      </aside>

      {/* Sign-in form — a card floating on the dark ground, centred in the panel. */}
      <div className="flex items-center justify-center px-6 py-12 sm:px-10">
        <div className="animate-in w-full max-w-md">
          {/* Compact brand for small screens (the brand panel is hidden below md). */}
          <div className="mb-8 md:hidden">
            <BrandMark variant="wordmark" height={26} />
          </div>

          <div
            className="rounded-2xl border p-8 shadow-2xl"
            style={{ background: 'var(--login-card)', borderColor: 'var(--login-hair)' }}
          >
            <h1 className="font-display text-2xl font-extrabold tracking-tight text-[var(--login-text)]">
              Welcome back
            </h1>
            <p className="mt-1 text-sm text-[var(--login-text-dim)]">Sign in to your incident console.</p>

            {ssoEnabled && (
              <>
                <button
                  type="button"
                  onClick={signInWithMicrosoft}
                  disabled={submitting}
                  className="mt-6 flex w-full items-center justify-center gap-2.5 rounded-xl border border-[var(--login-hair)] bg-white/[0.06] px-4 py-3 text-sm font-semibold text-[var(--login-text)] transition hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <MicrosoftLogo /> Sign in with Microsoft
                </button>
                <div className="mt-5 flex items-center gap-3">
                  <span className="h-px flex-1 bg-[var(--login-hair)]" />
                  <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--login-text-dim)]">
                    or
                  </span>
                  <span className="h-px flex-1 bg-[var(--login-hair)]" />
                </div>
              </>
            )}

            <form className="mt-6 space-y-4" onSubmit={submit}>
              <div className="space-y-1">
                <label htmlFor="username" className="text-xs font-medium text-[var(--login-text-dim)]">
                  Username
                </label>
                <input
                  id="username"
                  type="text"
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="jdoe"
                  className={inputClass}
                />
              </div>

              <div className="space-y-1">
                <label htmlFor="password" className="text-xs font-medium text-[var(--login-text-dim)]">
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
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-lg p-1.5 text-[var(--login-text-dim)] transition hover:text-[var(--login-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--login-accent)]"
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
                    className="relative h-5 w-9 shrink-0 rounded-full border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--login-accent)] focus-visible:ring-offset-2"
                    style={{
                      background: remember ? 'var(--login-accent)' : 'rgba(255,255,255,0.08)',
                      borderColor: remember ? 'var(--login-accent)' : 'var(--login-hair)',
                    }}
                  >
                    <span
                      className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
                        remember ? 'left-[18px]' : 'left-0.5'
                      }`}
                    />
                  </button>
                  <span className="text-sm text-[var(--login-text-dim)]">Remember me</span>
                </label>
              </div>

              {error && <p className="text-sm text-[var(--sev-critical)]">{error}</p>}

              <Button
                type="submit"
                className="w-full border-0 py-3 text-white"
                disabled={submitting}
                style={{
                  background: 'linear-gradient(135deg, var(--login-accent), var(--login-accent-strong))',
                }}
              >
                {submitting ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </div>
        </div>
      </div>
    </div>
  )
}
