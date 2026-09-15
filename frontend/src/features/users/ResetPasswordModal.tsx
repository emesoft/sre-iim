import { useState } from 'react'
import { api, errText } from '../../lib/api'
import type { UserOut } from '../../lib/types'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/Button'
import { Field } from '../../components/ui/Field'

const inputCls =
  'w-full rounded-lg border border-hair bg-plane p-1.5 text-sm text-ink outline-none focus:border-accent'

export function ResetPasswordModal({
  user,
  onClose,
  onReset,
}: {
  user: UserOut | null
  onClose: () => void
  onReset: () => void
}) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const close = () => {
    setPassword('')
    setErr(null)
    onClose()
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!user) return
    setErr(null)
    setBusy(true)
    try {
      await api.post(`/api/users/${user.id}/password`, { new_password: password })
      onReset()
      close()
    } catch (e) {
      setErr(errText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={!!user} title={`Reset password — ${user?.username ?? ''}`} onClose={close}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field label="New password">
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
        {err && <p className="text-xs text-sev-critical">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? 'Resetting…' : 'Reset password'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
