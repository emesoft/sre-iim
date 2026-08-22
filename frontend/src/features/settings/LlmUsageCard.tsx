import { useEffect, useState } from 'react'
import { api, errText } from '../../lib/api'
import type { LlmUsage } from '../../lib/types'

/**
 * Real LLM token spend since the app started tracking it — excludes cache hits (no LLM call was
 * made) and providers that don't report usage. Currently only claude_cli is instrumented, since
 * that's the provider actually used for this demo (see backend/app/infrastructure/llm/claude_cli.py).
 */
export function LlmUsageCard() {
  const [usage, setUsage] = useState<LlmUsage | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<LlmUsage>('/api/settings/llm-usage')
      .then(setUsage)
      .catch((e) => setError(errText(e)))
  }, [])

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-hair p-4">
      <h3 className="text-sm font-semibold text-muted">LLM usage</h3>
      {error && <p className="text-xs text-sev-critical">{error}</p>}
      {!error && !usage && <p className="text-xs text-muted">Loading…</p>}
      {usage && usage.by_model.length === 0 && (
        <p className="text-xs text-muted">No tracked usage yet.</p>
      )}
      {usage && usage.by_model.length > 0 && (
        <>
          <p className="text-sm text-ink">
            <span className="font-mono font-semibold">{usage.total_input_tokens.toLocaleString()}</span>{' '}
            input /{' '}
            <span className="font-mono font-semibold">{usage.total_output_tokens.toLocaleString()}</span>{' '}
            output tokens total
          </p>
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1">Model</th>
                <th>Analyses</th>
                <th>Input</th>
                <th>Output</th>
              </tr>
            </thead>
            <tbody>
              {usage.by_model.map((m) => (
                <tr key={m.model_id} className="border-t border-hair">
                  <td className="py-1 font-mono text-ink-2">{m.model_id}</td>
                  <td className="text-ink-2">{m.analyses_count}</td>
                  <td className="text-ink-2">{m.input_tokens.toLocaleString()}</td>
                  <td className="text-ink-2">{m.output_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
