import { useEffect, useRef, useState } from 'react'
import { MessageSquare, RotateCcw, Send } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { ChatMessageOut, IncidentDetail } from '../../lib/types'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'

/**
 * Opening questions, built from what this incident actually has.
 *
 * A blank prompt box is the wrong thing to hand someone at 3am: the useful questions are specific
 * to the evidence present, and the person reading the page has to invent them under pressure. Each
 * suggestion below is gated on the data that makes it answerable — offering "check the logs" for an
 * incident with no log group just sends Claude looking for something that isn't there.
 */
function suggestedQuestions(incident: IncidentDetail): string[] {
  const ctx = (incident.context ?? {}) as Record<string, any>
  const out: string[] = []

  // First, always. The TargetTracking scale-in alarms made the case: the most valuable triage
  // question is often whether anything is wrong at all.
  out.push('Is this a real problem, or expected behaviour?')

  if (ctx.ecs?.service)
    out.push(`What changed on ${ctx.ecs.service} around then — a deploy, or scaling?`)

  if (incident.log_group || incident.service)
    out.push('Show me the error logs from the 30 minutes before this fired')

  if (incident.analysis?.known_issue)
    out.push('How was the similar past incident resolved?')

  out.push(
    incident.analysis
      ? 'What is the fastest way to confirm the root cause?'
      : 'Summarise what we know so far and what is missing',
  )

  return out
}

export function ChatPanel({
  incident,
  canChat,
}: {
  incident: IncidentDetail
  canChat: boolean
}) {
  const [messages, setMessages] = useState<ChatMessageOut[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setErr(null)
    setLoading(true)
    api
      .get<ChatMessageOut[]>(`/api/incidents/${incident.id}/chat`)
      .then(setMessages)
      .catch((e) => setErr(errText(e)))
      .finally(() => setLoading(false))
  }, [incident.id])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const reset = async () => {
    if (!confirm('Start this conversation over? The transcript is deleted.')) return
    setErr(null)
    try {
      await api.del(`/api/incidents/${incident.id}/chat`)
      setMessages([])
    } catch (e) {
      setErr(errText(e))
    }
  }

  const send = (e: React.FormEvent) => {
    e.preventDefault()
    ask(input)
  }

  // One path for both the form and the suggestion chips: a chip that took a different route would
  // be a second place for the optimistic bubble and the failure handling to drift.
  const ask = (raw: string) => {
    const text = raw.trim()
    if (!text || sending) return
    setSending(true)
    setErr(null)
    const optimisticUser: ChatMessageOut = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: text,
      input_tokens: null,
      cached_input_tokens: null,
      output_tokens: null,
      created_at: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, optimisticUser])
    setInput('')
    api
      .post<ChatMessageOut>(`/api/incidents/${incident.id}/chat`, { message: text })
      .then((reply) => setMessages((prev) => [...prev, reply]))
      .catch((e) => {
        // Send failed — drop the optimistic bubble so it doesn't look sent, and give the
        // text back to the user instead of silently losing it.
        setMessages((prev) => prev.filter((m) => m.id !== optimisticUser.id))
        setInput(optimisticUser.content)
        setErr(errText(e))
      })
      .finally(() => setSending(false))
  }

  return (
    <Card className="p-5">
      <div className="flex items-center gap-2">
        <MessageSquare size={15} className="text-accent" />
        <h3 className="font-display text-sm font-bold text-ink">Chat with Claude</h3>
        {canChat && messages.length > 0 && (
          <button
            onClick={reset}
            disabled={sending}
            title="Start over — a conversation keeps the tools it began with, so an old one can be unaware of newer ones"
            className="ml-auto flex items-center gap-1 text-xs font-semibold text-muted transition hover:text-accent disabled:opacity-50"
          >
            <RotateCcw size={12} /> Start over
          </button>
        )}
      </div>

      <div className="mt-3 max-h-96 space-y-3 overflow-y-auto">
        {loading && <p className="text-sm text-muted">Loading…</p>}
        {!loading && messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-muted">
              Ask anything about this incident, or start with one of these:
            </p>
            {canChat && (
              <div className="flex flex-wrap gap-1.5">
                {suggestedQuestions(incident).map((q) => (
                  <button
                    key={q}
                    onClick={() => ask(q)}
                    disabled={sending}
                    className="rounded-full border border-hair bg-surface-2 px-3 py-1.5 text-left text-xs text-ink-2 transition hover:border-accent hover:text-accent disabled:opacity-50"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                m.role === 'user' ? 'bg-accent text-white' : 'bg-surface-2 text-ink'
              }`}
            >
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.role === 'assistant' && m.input_tokens !== null && m.output_tokens !== null && (
                // Split, not summed. Nearly all of a turn's input is the cached prefix re-read
                // each time; one "29,375 in" figure reads as fresh spend and is wrong by about an
                // order of magnitude.
                <p className="mt-1 font-mono text-[10px] opacity-70">
                  {m.input_tokens.toLocaleString()} new
                  {m.cached_input_tokens ? ` · ${m.cached_input_tokens.toLocaleString()} cached` : ''}
                  {' · '}
                  {m.output_tokens.toLocaleString()} out
                </p>
              )}
            </div>
          </div>
        ))}
        {sending && <p className="text-xs text-muted">Claude is thinking…</p>}
        <div ref={bottomRef} />
      </div>

      {err && <p className="mt-2 text-sm text-sev-critical">{err}</p>}

      {canChat ? (
        <form onSubmit={send} className="mt-3 flex items-center gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about this incident…"
            disabled={sending}
            className="flex-1 rounded-xl border border-hair bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
          <Button type="submit" disabled={sending || !input.trim()}>
            <Send size={15} /> Send
          </Button>
        </form>
      ) : (
        <p className="mt-3 text-xs text-muted">Read-only access — chat is available to admin/sre.</p>
      )}
    </Card>
  )
}
