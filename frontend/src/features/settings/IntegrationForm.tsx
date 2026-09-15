import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { AwsSsoConnect } from './AwsSsoConnect'
import type { Integration, IntegrationCreate, Provider } from '../../lib/types'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/Button'

const inputClass =
  'w-full rounded-lg border border-hair bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent'

/** Turns `sso_profile_name` into "Sso profile name" — good enough while field names stay
 * self-describing, and it means a new provider needs no frontend copy. */
function humanize(key: string): string {
  const text = key.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/**
 * Add/edit an integration. Every field comes from the provider catalog (`GET /api/providers`),
 * which is generated from the backend registry — so a new provider shows up here with the right
 * config fields, secrets and capability toggles without a line of frontend change.
 *
 * Secrets are write-only: an existing one shows as "stored" and is left alone unless retyped,
 * which is why editing a region never demands a PAT nobody has to hand.
 */
export function IntegrationForm({
  providers,
  projects,
  project,
  existing,
  onClose,
  onSaved,
}: {
  providers: Provider[]
  projects: string[]
  project: string
  existing: Integration | null
  onClose: () => void
  onSaved: () => void
}) {
  const editing = existing !== null
  const [providerId, setProviderId] = useState(existing?.provider ?? providers[0]?.provider ?? '')
  const provider = providers.find((p) => p.provider === providerId) ?? null

  const [form, setForm] = useState({
    project: existing?.project ?? project,
    env: existing?.env ?? 'prod',
    display_name: existing?.display_name ?? '',
  })
  const [config, setConfig] = useState<Record<string, string>>(existing?.config ?? {})
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [capabilities, setCapabilities] = useState<string[]>(
    existing?.capabilities ?? provider?.capabilities ?? [],
  )
  // Which credentials a person can actually type. A provider declares every secret it may hold,
  // but for AWS that set spans three mutually exclusive auth types — and three of them are written
  // by the SSO flow, never entered. Rendering all of them offered empty "Sso refresh token" boxes
  // next to a button whose whole job is to obtain one.
  const typeableSecrets = (provider?.secret_names ?? []).filter((name) => {
    if (providerId !== 'aws') return true
    if (config.auth_type === 'access_key') {
      return name === 'access_key_id' || name === 'secret_access_key'
    }
    // `sso` reads a host profile and `sso_oidc` signs in; neither takes a typed secret.
    return false
  })

  // Already signed in? The credential is there, so offering the sign-in panel again would invite
  // people to redo a working connection. Keyed off the stored secret rather than the auth type,
  // because switching an access-key connection to SSO has the type set and no credential yet.
  const ssoConnected =
    config.auth_type === 'sso_oidc' && (existing?.secret_names.includes('sso_refresh_token') ?? false)

  // An SSO connection renews itself only until the SSO instance's session policy runs out; after
  // that the refresh token is dead and nothing but a fresh browser approval brings it back. That
  // makes re-signing-in a routine act, not an exceptional one, so it needs a control — the status
  // note used to say "re-run the sign-in if it stops working" next to no way to do that.
  const [reconnecting, setReconnecting] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pickProvider = (next: string) => {
    setProviderId(next)
    // Config and secrets are shaped for a provider; carrying them across would submit nonsense.
    setConfig({})
    setSecrets({})
    setCapabilities(providers.find((p) => p.provider === next)?.capabilities ?? [])
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const body: IntegrationCreate = {
      project: form.project,
      env: form.env.trim() || 'all',
      provider: providerId,
      display_name: form.display_name.trim() || null,
      config,
      secrets,
      capabilities,
    }
    try {
      if (editing) {
        await api.patch<Integration>(`/api/integrations/${existing.id}`, body)
      } else {
        await api.post<Integration>('/api/integrations', body)
      }
      onSaved()
    } catch (err) {
      setError(errText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={editing ? 'Edit integration' : 'Add integration'}>
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-xs font-medium text-ink-2">Project</span>
            <select
              value={form.project}
              onChange={(e) => setForm({ ...form, project: e.target.value })}
              className={inputClass}
            >
              {projects.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-ink-2">Environment</span>
            <input
              value={form.env}
              onChange={(e) => setForm({ ...form, env: e.target.value })}
              placeholder="prod / qa / all"
              className={inputClass}
            />
          </label>
        </div>

        <label className="space-y-1 block">
          <span className="text-xs font-medium text-ink-2">Provider</span>
          <select
            value={providerId}
            onChange={(e) => pickProvider(e.target.value)}
            // Changing it would orphan config and secrets shaped for the old one; the backend
            // ignores the field on update, so the control matches that.
            disabled={editing}
            className={`${inputClass} disabled:opacity-60`}
          >
            {providers.map((p) => (
              <option key={p.provider} value={p.provider}>
                {p.label}
              </option>
            ))}
          </select>
          {provider?.notes && <span className="text-xs text-muted">{provider.notes}</span>}
        </label>

        {provider && (
          <>
            <div className="space-y-2">
              {provider.required_config.map((key) =>
                // The one config key with a fixed, meaningful set of values. As a free-text box it
                // asked people to type `sso_oidc` from memory, and each option differs in what it
                // needs from the host — which the labels have to say, because that is the whole
                // basis for choosing between them.
                providerId === 'aws' && key === 'auth_type' ? (
                  <label key={key} className="space-y-1 block">
                    <span className="text-xs font-medium text-ink-2">How to authenticate</span>
                    <select
                      value={config[key] ?? ''}
                      onChange={(e) => setConfig({ ...config, [key]: e.target.value })}
                      className={inputClass}
                      required
                    >
                      <option value="">Choose…</option>
                      <option value="sso_oidc">Sign in with AWS SSO — nothing needed on this machine</option>
                      <option value="access_key">Access key pair — long-lived, works anywhere</option>
                      <option value="sso">Local SSO profile — needs ~/.aws on the host</option>
                    </select>
                  </label>
                ) : (
                  <label key={key} className="space-y-1 block">
                    <span className="text-xs font-medium text-ink-2">{humanize(key)}</span>
                    <input
                      value={config[key] ?? ''}
                      onChange={(e) => setConfig({ ...config, [key]: e.target.value })}
                      className={inputClass}
                      required
                    />
                  </label>
                ),
              )}
              {/* Signing in from here is the only auth type that survives a fresh deployment:
                  the other two need a profile on the host or a long-lived key someone carries. */}
              {providerId === 'aws' && config.auth_type === 'sso_oidc' && ssoConnected &&
                !reconnecting && (
                  <div className="space-y-2 rounded-xl border border-hair bg-plane p-3">
                    <p className="text-xs text-ink-2">
                      Signed in and pinned to{' '}
                      <span className="font-mono text-ink">{config.account_id}</span> ·{' '}
                      <span className="font-mono text-ink">{config.role_name}</span>. It renews
                      itself until your SSO session policy runs out; after that it needs approving
                      in a browser again.
                    </p>
                    {/* The Test button reports an expired session as "reconnect it on Settings" —
                        this is the control that sentence is pointing at. */}
                    <button
                      type="button"
                      onClick={() => setReconnecting(true)}
                      className="text-xs font-semibold text-accent hover:opacity-80"
                    >
                      Sign in again
                    </button>
                  </div>
                )}
              {providerId === 'aws' && config.auth_type === 'sso_oidc' &&
                (!ssoConnected || reconnecting) && (
                  <div className="space-y-2">
                    <AwsSsoConnect
                      integrationId={existing?.id}
                      project={form.project}
                      env={form.env}
                      region={config.region ?? ''}
                      capabilities={capabilities}
                      displayName={form.display_name.trim() || null}
                      // Only on a reconnect: a first-time connection has none of this stored yet,
                      // and the form is where those two values get typed for the only time.
                      defaultStartUrl={reconnecting ? (config.sso_start_url ?? '') : ''}
                      defaultRegion={reconnecting ? (config.sso_region ?? '') : ''}
                      autoPick={
                        reconnecting && config.account_id && config.role_name
                          ? { accountId: config.account_id, roleName: config.role_name }
                          : undefined
                      }
                      onConnected={onSaved}
                    />
                    {reconnecting && (
                      <button
                        type="button"
                        onClick={() => setReconnecting(false)}
                        className="text-xs font-semibold text-muted hover:text-ink"
                      >
                        Keep the current sign-in
                      </button>
                    )}
                  </div>
                )}
              {providerId === 'aws' && config.auth_type === 'sso' && (
                <label className="space-y-1 block">
                  <span className="text-xs font-medium text-ink-2">Sso profile name</span>
                  <input
                    value={config.sso_profile_name ?? ''}
                    onChange={(e) => setConfig({ ...config, sso_profile_name: e.target.value })}
                    placeholder="gcm-prod-read"
                    className={inputClass}
                  />
                </label>
              )}
            </div>

            {typeableSecrets.length > 0 && (
              <div className="space-y-2">
                {typeableSecrets.map((name) => {
                  const stored = existing?.secret_names.includes(name)
                  return (
                    <label key={name} className="space-y-1 block">
                      <span className="text-xs font-medium text-ink-2">
                        {humanize(name)}
                        {stored && <span className="ml-1 text-muted">· stored, leave blank to keep</span>}
                      </span>
                      <input
                        type="password"
                        value={secrets[name] ?? ''}
                        onChange={(e) => setSecrets({ ...secrets, [name]: e.target.value })}
                        autoComplete="new-password"
                        className={inputClass}
                      />
                    </label>
                  )
                })}
              </div>
            )}

            <div className="space-y-1">
              <span className="text-xs font-medium text-ink-2">Capabilities</span>
              <div className="flex flex-wrap gap-2">
                {provider.capabilities.map((capability) => {
                  const on = capabilities.includes(capability)
                  return (
                    <button
                      key={capability}
                      type="button"
                      onClick={() =>
                        setCapabilities(
                          on
                            ? capabilities.filter((c) => c !== capability)
                            : [...capabilities, capability],
                        )
                      }
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                        on
                          ? 'border-accent bg-accent-weak text-accent'
                          : 'border-hair bg-surface text-muted hover:text-ink-2'
                      }`}
                    >
                      {capability}
                    </button>
                  )
                })}
              </div>
            </div>
          </>
        )}

        {error && <p className="text-sm text-sev-critical">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            // The SSO flow creates the integration when a role is picked; a second Save here would
            // try to write one with no credentials attached.
            // Saving an SSO connection before signing in would store a config with no credential
            // behind it — an integration that looks configured and fails on first use.
            disabled={saving || !provider || (config.auth_type === 'sso_oidc' && !ssoConnected)}
          >
            {saving ? 'Saving…' : editing ? 'Save changes' : 'Add integration'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
