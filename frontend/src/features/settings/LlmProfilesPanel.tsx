import { useEffect, useState } from 'react'
import { CheckCircle2, Pencil, Plus, Star, Trash2, XCircle } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type {
  Integration,
  LlmField,
  LlmProfile,
  LlmSetup,
  Project,
  TestConnectionResult,
} from '../../lib/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Skeleton } from '../../components/ui/Skeleton'

const inputCls =
  'w-full rounded-lg border border-hair bg-plane px-3 py-2 text-sm text-ink outline-none focus:border-accent'

/**
 * Which model analyses which project.
 *
 * A profile is a named provider + credential; one is the default and every project follows it
 * unless pointed somewhere else. That indirection is the point: a key shared by ten projects is
 * stored once, rotated once, and the overrides table answers "what is this key used for" at a
 * glance — which a provider dropdown repeated on every project never could.
 */
export function LlmProfilesPanel({ projects }: { projects: Project[] }) {
  const [setup, setSetup] = useState<LlmSetup | null>(null)
  const [awsIntegrations, setAwsIntegrations] = useState<Integration[]>([])
  const [editing, setEditing] = useState<LlmProfile | 'new' | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tests, setTests] = useState<Record<string, TestConnectionResult>>({})

  const load = async () => {
    try {
      setSetup(await api.get<LlmSetup>('/api/settings/llm'))
      setError(null)
    } catch (e) {
      setError(errText(e))
    }
  }

  useEffect(() => {
    load()
    api
      .get<Integration[]>('/api/integrations')
      .then((rows) => setAwsIntegrations(rows.filter((r) => r.provider === 'aws')))
      .catch(() => setAwsIntegrations([]))
  }, [])

  if (!setup) return <Skeleton className="h-64 rounded-2xl" />

  const labelOf = (key: string) => setup.providers.find((p) => p.key === key)?.label ?? key

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setBusyId(id)
    setError(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusyId(null)
    }
  }

  const runTest = async (profile: LlmProfile) => {
    setBusyId(profile.id)
    try {
      const result = await api.post<TestConnectionResult>(
        `/api/settings/llm/profiles/${profile.id}/test`,
        {},
      )
      setTests((t) => ({ ...t, [profile.id]: result }))
    } catch (e) {
      setTests((t) => ({ ...t, [profile.id]: { ok: false, error: errText(e) } }))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-2xl border border-hair bg-surface p-5 shadow-card">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-display text-base font-bold text-ink">Model profiles</h3>
            <p className="mt-1 text-xs leading-relaxed text-ink-2">
              Each profile is one provider and one credential. The default analyses every project
              that hasn&rsquo;t been pointed somewhere else.
            </p>
          </div>
          <Button onClick={() => setEditing('new')}>
            <Plus size={15} /> New profile
          </Button>
        </div>

        {error && <p className="mt-3 text-sm text-sev-critical">{error}</p>}

        <div className="mt-4 divide-y divide-hair rounded-xl border border-hair">
          {setup.profiles.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted">
              No profiles yet — analysis is using the provider from the environment.
            </p>
          ) : (
            setup.profiles.map((profile) => {
              const isDefault = profile.id === setup.default_profile_id
              const using = Object.entries(setup.by_project)
                .filter(([, id]) => id === profile.id)
                .map(([project]) => project)
              const test = tests[profile.id]
              return (
                <div key={profile.id} className="px-4 py-3">
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-ink">{profile.name}</span>
                        <Badge tone="neutral">{labelOf(profile.provider)}</Badge>
                        {isDefault && <Badge tone="accent">default</Badge>}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                        <span>
                          {profile.config.model || 'model from the environment'}
                          {profile.secret_names.length > 0 && ' · credential stored'}
                          {using.length > 0 && ` · also used by ${using.join(', ')}`}
                        </span>
                        {/* A typed model id is only proven by a real call — the credential can be
                            perfect and the run still fail on a name nothing validates up front. */}
                        {profile.last_test_ok === null && <Badge tone="warning">never tested</Badge>}
                        {profile.last_test_ok === false && (
                          <span title={profile.last_test_error ?? undefined}>
                            <Badge tone="danger">last test failed</Badge>
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {!isDefault && (
                        <Button
                          variant="ghost"
                          disabled={busyId === profile.id}
                          title="Make this the default for every project"
                          onClick={() =>
                            act(profile.id, () =>
                              api.put('/api/settings/llm/default', { profile_id: profile.id }),
                            )
                          }
                        >
                          <Star size={14} />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        disabled={busyId === profile.id}
                        onClick={() => runTest(profile)}
                      >
                        Test
                      </Button>
                      <Button variant="ghost" onClick={() => setEditing(profile)}>
                        <Pencil size={14} />
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={busyId === profile.id}
                        onClick={() => {
                          if (
                            !confirm(
                              `Delete "${profile.name}"?` +
                                (using.length
                                  ? ` ${using.join(', ')} will fall back to the default.`
                                  : ''),
                            )
                          )
                            return
                          act(profile.id, () => api.del(`/api/settings/llm/profiles/${profile.id}`))
                        }}
                      >
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </div>
                  {test && (
                    <p
                      className={`mt-2 flex items-center gap-1.5 text-xs ${
                        test.ok ? 'text-sev-low' : 'text-sev-critical'
                      }`}
                    >
                      {test.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                      {test.ok ? 'The model answered — credentials work.' : test.error}
                    </p>
                  )}
                </div>
              )
            })
          )}
        </div>
      </section>

      {setup.profiles.length > 0 && projects.length > 0 && (
        <section className="rounded-2xl border border-hair bg-surface p-5 shadow-card">
          <h3 className="font-display text-base font-bold text-ink">Per-project model</h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-2">
            Leave a project on the default unless it needs its own — a customer whose data has to
            stay on their own account, say.
          </p>
          <div className="mt-4 divide-y divide-hair rounded-xl border border-hair">
            {projects.map((project) => {
              const overrideId = setup.by_project[project.name] ?? ''
              return (
                <div key={project.id} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="flex-1 text-sm font-medium text-ink">{project.name}</span>
                  <select
                    aria-label={`Model for ${project.name}`}
                    value={overrideId}
                    disabled={busyId === project.name}
                    onChange={(e) =>
                      act(project.name, () =>
                        api.put(`/api/settings/llm/projects/${project.name}`, {
                          profile_id: e.target.value || null,
                        }),
                      )
                    }
                    className="rounded-lg border border-hair bg-plane px-2 py-1 text-xs font-semibold text-ink outline-none focus:border-accent disabled:opacity-50"
                  >
                    <option value="">
                      Default
                      {setup.default_profile_id
                        ? ` — ${setup.profiles.find((p) => p.id === setup.default_profile_id)?.name}`
                        : ''}
                    </option>
                    {setup.profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {editing && (
        <ProfileForm
          profile={editing === 'new' ? null : editing}
          setup={setup}
          awsIntegrations={awsIntegrations}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            await load()
          }}
        />
      )}
    </div>
  )
}

function ProfileForm({
  profile,
  setup,
  awsIntegrations,
  onClose,
  onSaved,
}: {
  profile: LlmProfile | null
  setup: LlmSetup
  awsIntegrations: Integration[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(profile?.name ?? '')
  const [provider, setProvider] = useState(profile?.provider ?? setup.providers[0]?.key ?? '')
  const [values, setValues] = useState<Record<string, string>>(profile?.config ?? {})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const spec = setup.providers.find((p) => p.key === provider)

  const pickProvider = (key: string) => {
    setProvider(key)
    const next = setup.providers.find((p) => p.key === key)
    setValues(key === profile?.provider ? profile.config : { ...(next?.defaults ?? {}) })
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const secretNames = (spec?.fields ?? []).filter((f) => f.secret).map((f) => f.name)
    const config: Record<string, string> = {}
    const secrets: Record<string, string> = {}
    for (const [k, v] of Object.entries(values)) {
      if (secretNames.includes(k)) secrets[k] = v
      else config[k] = v
    }
    const body = { name: name.trim(), provider, config, secrets }
    try {
      if (profile) await api.patch(`/api/settings/llm/profiles/${profile.id}`, body)
      else await api.post('/api/settings/llm/profiles', body)
      onSaved()
    } catch (err) {
      setError(errText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={profile ? `Edit ${profile.name}` : 'New model profile'}>
      <form onSubmit={submit} className="space-y-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-2">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Anthropic — production key"
            required
            autoFocus
            className={inputCls}
          />
          <span className="block text-xs text-muted">
            What it&rsquo;s for, not which vendor — that&rsquo;s below.
          </span>
        </label>

        <div className="space-y-1">
          <span className="text-xs font-medium text-ink-2">Provider</span>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {setup.providers.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => pickProvider(p.key)}
                className={`rounded-xl border p-3 text-left transition ${
                  p.key === provider ? 'border-accent bg-accent-weak' : 'border-hair hover:border-ink-2'
                }`}
              >
                <span className="block text-sm font-semibold text-ink">{p.label}</span>
                <span className="mt-1 block text-xs leading-relaxed text-muted">{p.notes}</span>
              </button>
            ))}
          </div>
        </div>

        {(spec?.fields ?? []).map((f) => (
          <FieldRow
            key={f.name}
            field={f}
            value={values[f.name] ?? ''}
            stored={(profile?.secret_names ?? []).includes(f.name)}
            awsIntegrations={awsIntegrations}
            onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
          />
        ))}

        {error && <p className="text-sm text-sev-critical">{error}</p>}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : profile ? 'Save' : 'Create profile'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function FieldRow({
  field,
  value,
  stored,
  awsIntegrations,
  onChange,
}: {
  field: LlmField
  value: string
  stored: boolean
  awsIntegrations: Integration[]
  onChange: (v: string) => void
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-center gap-2 text-xs font-medium text-ink-2">
        {field.label}
        {stored && (
          <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-sev-low">
            stored
          </span>
        )}
      </span>
      {field.source === 'aws_integration' ? (
        <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls}>
          <option value="">Environment / instance role</option>
          {awsIntegrations.map((i) => (
            <option key={i.id} value={i.id}>
              {i.project} · {i.env}
              {i.display_name ? ` — ${i.display_name}` : ''}
            </option>
          ))}
        </select>
      ) : field.options.length > 0 ? (
        <ModelPicker field={field} value={value} onChange={onChange} />
      ) : (
        <input
          type={field.secret ? 'password' : 'text'}
          value={value}
          autoComplete="off"
          placeholder={stored ? 'Leave blank to keep the stored one' : field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        />
      )}
      {field.help && <span className="block text-xs leading-relaxed text-muted">{field.help}</span>}
    </label>
  )
}

/**
 * A dropdown of known-good values with an "Other…" escape hatch.
 *
 * The list isn't a whitelist — a model released this morning has to be usable this morning, and a
 * shipped container can't know about it. What the dropdown removes is the *routine* typo: picking
 * from a list is how you avoid putting a CLI alias in an API model box. Anything typed by hand
 * lands in a profile the list then flags as never tested until it has actually answered.
 */
function ModelPicker({
  field,
  value,
  onChange,
}: {
  field: LlmField
  value: string
  onChange: (v: string) => void
}) {
  const known = field.options.includes(value)
  const [custom, setCustom] = useState(!known && value !== '')

  if (custom)
    return (
      <div className="flex gap-2">
        <input
          value={value}
          autoComplete="off"
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        />
        <button
          type="button"
          onClick={() => {
            setCustom(false)
            onChange('')
          }}
          className="shrink-0 rounded-lg border border-hair px-2 text-xs font-semibold text-muted hover:text-ink-2"
        >
          Pick
        </button>
      </div>
    )

  return (
    <select
      value={value}
      onChange={(e) => {
        if (e.target.value === '__other__') {
          setCustom(true)
          onChange('')
        } else onChange(e.target.value)
      }}
      className={inputCls}
    >
      <option value="">{field.required ? 'Choose a model…' : 'Same as the main model'}</option>
      {field.options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
      <option value="__other__">Other…</option>
    </select>
  )
}
