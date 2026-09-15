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

  const unsplit = (usage?.by_model ?? []).reduce((n, m) => n + m.unsplit_input_tokens, 0)

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
          {/* The unsplit total is part of the header too. Left out, a table of nothing but
              pre-split rows reported "0 new input / 0 cached" above six figures of usage — a
              summary that contradicts the rows under it. */}
          <p className="text-sm text-ink">
            <span className="font-mono font-semibold">
              {(usage.total_input_tokens + unsplit).toLocaleString()}
            </span>{' '}
            input /{' '}
            <span className="font-mono font-semibold text-muted">
              {usage.total_cached_input_tokens.toLocaleString()}
            </span>{' '}
            cached /{' '}
            <span className="font-mono font-semibold">{usage.total_output_tokens.toLocaleString()}</span>{' '}
            output tokens total
          </p>
          <table className="w-full text-xs">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1">Model</th>
                <th>Billed to</th>
                {/* Not "Analyses": the chat row counts messages, and calling those analyses made
                    the column disagree with itself. */}
                <th>Calls</th>
                <th>New input</th>
                <th>Cached</th>
                <th>Output</th>
              </tr>
            </thead>
            <tbody>
              {usage.by_model.map((m) => (
                <tr
                  key={`${m.source}:${m.llm_profile ?? ''}:${m.model_id}`}
                  className="border-t border-hair"
                >
                  <td className="py-1 font-mono text-ink-2">{m.model_id}</td>
                  {/* Two cohorts can run the same model on different keys — the profile is the
                      only thing that tells their spend apart. */}
                  <td className="text-ink-2">
                    {m.llm_profile ?? (
                      // An em dash for both cases hid a real distinction: an analysis with no
                      // profile ran before profiles existed, while chat records none at all.
                      <span className="text-muted">
                        {m.source === 'analysis' ? 'Before profiles' : '—'}
                      </span>
                    )}
                  </td>
                  <td className="text-ink-2">{m.analyses_count}</td>
                  <td className="text-ink-2">
                    {m.unsplit_input_tokens > 0 ? (
                      // Recorded before fresh and cached were told apart. Shown in place of a
                      // fresh-input figure it cannot honestly provide, rather than being counted
                      // as fresh — which is the overstatement this column was split to fix.
                      <span
                        className="text-muted"
                        title="Recorded before cached tokens were tracked separately — this figure includes them."
                      >
                        {m.unsplit_input_tokens.toLocaleString()}*
                      </span>
                    ) : (
                      m.input_tokens.toLocaleString()
                    )}
                  </td>
                  {/* Its own column: cached tokens are real usage but priced far lower, and the
                      same prefix is re-sent on every call — added into Input they would dominate
                      the total and misstate the bill. */}
                  <td className="text-muted">{m.cached_input_tokens.toLocaleString()}</td>
                  <td className="text-ink-2">{m.output_tokens.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {usage.by_model.some((m) => m.unsplit_input_tokens > 0) && (
            <p className="mt-2 text-[11px] text-muted">
              * Recorded before cached tokens were tracked separately, so the figure includes them.
              New calls report the two apart.
            </p>
          )}
        </>
      )}
    </div>
  )
}
