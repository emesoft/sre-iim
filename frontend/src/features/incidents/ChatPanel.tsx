import { useEffect, useRef, useState } from 'react'
import { MessageSquare, Send } from 'lucide-react'
import { api, errText } from '../../lib/api'
import type { ChatMessageOut, IncidentDetail } from '../../lib/types'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'

export function ChatPanel({ incident }: { incident: IncidentDetail }) {
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

  const send = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending) return
    setSending(true)
    setErr(null)
    const optimisticUser: ChatMessageOut = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content: text,
      input_tokens: null,
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
      </div>

      <div className="mt-3 max-h-96 space-y-3 overflow-y-auto">
        {loading && <p className="text-sm text-muted">Loading…</p>}
        {!loading && messages.length === 0 && (
          <p className="text-sm text-muted">
            Ask about this incident — e.g. "check logs of that service around this time".
          </p>
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
                <p className="mt-1 font-mono text-[10px] opacity-70">
                  {m.input_tokens.toLocaleString()} in / {m.output_tokens.toLocaleString()} out
                </p>
              )}
            </div>
          </div>
        ))}
        {sending && <p className="text-xs text-muted">Claude is thinking…</p>}
        <div ref={bottomRef} />
      </div>

      {err && <p className="mt-2 text-sm text-sev-critical">{err}</p>}

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
    </Card>
  )
}
