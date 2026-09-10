import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { Role, UserOut } from '../../lib/types'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'

const inputCls =
  'w-full rounded-lg border border-hair bg-plane p-1.5 text-sm text-ink outline-none focus:border-accent'

const ROLES: Role[] = ['admin', 'sre', 'consultant']

export function CreateUserModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  onCreated: (user: UserOut) => void
}) {
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('consultant')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const reset = () => {
    setUsername('')
    setEmail('')
    setPassword('')
    setRole('consultant')
    setErr(null)
  }

  const close = () => {
    reset()
    onClose()
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(null)
    setBusy(true)
    try {
      const user = await api.post<UserOut>('/api/users', {
        username: username.trim(),
        email: email.trim() || null,
        password,
        role,
      })
      onCreated(user)
      close()
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} title="New user" onClose={close}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field label="Username">
          <input
            type="text"
            required
            autoComplete="off"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="jdoe"
            className={inputCls}
          />
        </Field>
        <Field label="Email (optional)">
          <input
            type="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@company.com"
            className={inputCls}
          />
        </Field>
        <Field label="Password">
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label="Group">
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} className={inputCls}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
        {err && <p className="text-xs text-sev-critical">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create user'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
