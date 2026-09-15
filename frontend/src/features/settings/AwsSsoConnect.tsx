import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ExternalLink, Loader2 } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { Integration, SsoBegin, SsoPoll, SsoRole } from '../../lib/types'
import { Button } from '../../components/ui/Button'

const inputClass =
  'w-full rounded-lg border border-hair bg-plane p-1.5 text-sm text-ink outline-none focus:border-accent'

/**
 * Connect an AWS account by signing in, rather than by pasting keys or naming a local profile.
 *
 * The other two auth types both assume something about the machine: a profile expects
 * `~/.aws/config` to exist and to be kept signed in from a terminal, and an access key is a
 * long-lived secret someone has to create and carry. Neither survives "deploy this to a new
 * server" — which is the case this exists for. The two fields below come from the AWS access
 * portal page, not from anything on the host.
 */
export function AwsSsoConnect({
  integrationId,
  project,
  env,
  region,
  capabilities,
  displayName,
  onConnected,
}: {
  /** Set when switching an existing connection over to SSO — without it the flow would add a
   * second AWS integration for the project rather than replacing this one's credentials. */
  integrationId?: string
  project: string
  env: string
  region: string
  capabilities: string[]
  displayName: string | null
  onConnected: (created: Integration) => void
}) {
  const [startUrl, setStartUrl] = useState('')
  const [ssoRegion, setSsoRegion] = useState('')
  const [onlyAccounts, setOnlyAccounts] = useState('')
  const [begun, setBegun] = useState<SsoBegin | null>(null)
  const [roles, setRoles] = useState<SsoRole[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => () => void (timer.current && window.clearTimeout(timer.current)), [])

  const begin = async () => {
    setBusy(true)
    setError(null)
    setRoles(null)
    try {
      const started = await api.post<SsoBegin>('/api/integrations/aws-sso/begin', {
        start_url: startUrl.trim(),
        sso_region: ssoRegion.trim(),
        only_accounts: onlyAccounts.trim() || null,
      })
      setBegun(started)
      // Opened for them, but the link stays on screen: a popup blocker silently swallowing this
      // would otherwise look like the sign-in simply never started.
      window.open(started.verification_uri_complete, '_blank', 'noopener')
      poll(started)
    } catch (e) {
      setError(errText(e))
      setBusy(false)
    }
  }

  const poll = (started: SsoBegin) => {
    const tick = async () => {
      try {
        const result = await api.post<SsoPoll>(
          `/api/integrations/aws-sso/${started.handle}/poll`,
          {},
        )
        if (result.status === 'pending') {
          // At the interval AWS asked for — polling faster earns a throttle, not a faster answer.
          timer.current = window.setTimeout(tick, started.interval_seconds * 1000)
          return
        }
        if (result.status === 'expired') {
          setError('That sign-in expired before it was approved. Start it again.')
          setBegun(null)
          setBusy(false)
          return
        }
        setRoles(result.roles)
        setBusy(false)
      } catch (e) {
        setError(errText(e))
        setBusy(false)
      }
    }
    tick()
  }

  const choose = async (role: SsoRole) => {
    if (!begun) return
    setBusy(true)
    setError(null)
    try {
      const created = await api.post<Integration>(
        `/api/integrations/aws-sso/${begun.handle}/finish`,
        {
          integration_id: integrationId ?? null,
          account_id: role.account_id,
          role_name: role.role_name,
          project,
          env,
          region,
          capabilities,
          display_name: displayName,
        },
      )
      onConnected(created)
    } catch (e) {
      setError(errText(e))
      setBusy(false)
    }
  }

  if (roles) {
    return (
      <div className="space-y-2 rounded-xl border border-hair bg-plane p-3">
        <p className="text-xs text-ink-2">
          Signed in. Pick the account and role this project should use — the connection is pinned
          to it, so it can never reach anything else.
        </p>
        <div className="max-h-56 space-y-1 overflow-y-auto">
          {roles.length === 0 && (
            <p className="text-xs text-muted">This sign-in can&rsquo;t reach any account.</p>
          )}
          {roles.map((role) => (
            <button
              key={`${role.account_id}:${role.role_name}`}
              type="button"
              disabled={busy}
              onClick={() => choose(role)}
              className="flex w-full items-center justify-between gap-3 rounded-lg border border-hair px-3 py-2 text-left text-xs transition hover:border-accent disabled:opacity-50"
            >
              <span className="min-w-0">
                <span className="block truncate font-semibold text-ink">{role.account_name}</span>
                <span className="block font-mono text-[10px] text-muted">{role.account_id}</span>
              </span>
              <span className="shrink-0 font-semibold text-accent">{role.role_name}</span>
            </button>
          ))}
        </div>
        {error && <p className="text-xs text-sev-critical">{error}</p>}
      </div>
    )
  }

  if (begun) {
    return (
      <div className="space-y-2 rounded-xl border border-hair bg-plane p-3 text-xs">
        <p className="flex items-center gap-2 text-ink-2">
          <Loader2 size={13} className="animate-spin" /> Waiting for you to approve in the browser…
        </p>
        <p className="text-muted">
          Confirm this code matches what AWS shows:{' '}
          <span className="font-mono font-bold text-ink">{begun.user_code}</span>
        </p>
        <a
          href={begun.verification_uri_complete}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-semibold text-accent hover:opacity-80"
        >
          Open the approval page <ExternalLink size={11} />
        </a>
        {error && <p className="text-sev-critical">{error}</p>}
      </div>
    )
  }

  return (
    <div className="space-y-2 rounded-xl border border-hair bg-plane p-3">
      <p className="text-xs text-ink-2">
        Sign in with AWS SSO. Nothing is needed on the machine running this — both values are on
        your AWS access portal page.
      </p>
      <label className="block space-y-1">
        <span className="text-xs font-medium text-ink-2">SSO start URL</span>
        <input
          value={startUrl}
          onChange={(e) => setStartUrl(e.target.value)}
          placeholder="https://d-xxxxxxxxxx.awsapps.com/start"
          className={inputClass}
        />
      </label>
      <label className="block space-y-1">
        <span className="text-xs font-medium text-ink-2">SSO region</span>
        <input
          value={ssoRegion}
          onChange={(e) => setSsoRegion(e.target.value)}
          placeholder="us-east-2"
          className={inputClass}
        />
      </label>
      <label className="block space-y-1">
        <span className="text-xs font-medium text-ink-2">
          Only these accounts <span className="text-muted">· optional</span>
        </span>
        <input
          value={onlyAccounts}
          onChange={(e) => setOnlyAccounts(e.target.value)}
          placeholder="847659741065, 800940621545"
          className={inputClass}
        />
        {/* Applied on the server, before the list is sent. Filtering in the browser would leave
            every account in the organisation sitting in the network tab, which is not what
            "only show these" means to the person asking for it. */}
        <span className="block text-xs leading-relaxed text-muted">
          Leave blank to list every account this sign-in can reach. Narrowing it here means the
          others are never sent to the browser at all.
        </span>
      </label>
      {error && <p className="text-xs text-sev-critical">{error}</p>}
      <Button
        type="button"
        disabled={busy || !startUrl.trim() || !ssoRegion.trim() || !project}
        onClick={begin}
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
        Connect with AWS SSO
      </Button>
      {!project && <p className="text-xs text-muted">Choose a project first.</p>}
    </div>
  )
}
