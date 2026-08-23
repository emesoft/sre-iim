import { useEffect, useState } from 'react'

const OTHER = '__other__'
const inputCls =
  'mt-1 w-full rounded-lg border border-hair bg-plane p-2 text-sm text-ink outline-none focus:border-accent'

/**
 * A dropdown of known values plus an "Other…" option that reveals a free-text input — lets the
 * admin pick a common value fast without hardcoding the full set (project/env/region are all
 * open-ended in practice; see CLAUDE.md's AWS SSO connect-in-app roadmap note for the same
 * flexibility need on the auth side).
 */
export function SelectOrOtherField({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: string[]
  value: string
  onChange: (value: string) => void
}) {
  const [choice, setChoice] = useState<string>(options.includes(value) ? value : OTHER)
  const [custom, setCustom] = useState<string>(options.includes(value) ? '' : value)

  // Resync when `value` changes from outside this component (e.g. the parent form loads a
  // different connection to edit) — the useState initializers above only run once on mount, so
  // without this the dropdown would keep showing whatever was selected first.
  useEffect(() => {
    setChoice(options.includes(value) ? value : OTHER)
    setCustom(options.includes(value) ? '' : value)
  }, [value, options])

  const selectChoice = (next: string) => {
    setChoice(next)
    onChange(next === OTHER ? custom : next)
  }

  const editCustom = (next: string) => {
    setCustom(next)
    onChange(next)
  }

  return (
    <label className="flex-1 text-sm text-ink-2">
      {label}
      <select value={choice} onChange={(e) => selectChoice(e.target.value)} className={inputCls}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
        <option value={OTHER}>Other…</option>
      </select>
      {choice === OTHER && (
        <input
          value={custom}
          onChange={(e) => editCustom(e.target.value)}
          placeholder={label}
          className={inputCls}
          required
        />
      )}
    </label>
  )
}
