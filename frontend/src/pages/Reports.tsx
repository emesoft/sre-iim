import { useEffect, useState } from 'react'
import { Check, Clipboard, FileText, ListChecks } from 'lucide-react'
import { api, errText } from '../lib/api'
import type { DailyReportOut } from '../lib/types'
import { Card, CardHeader } from '../components/ui/Card'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { SeverityBadge } from '../components/ui/SeverityBadge'
import { StatusBadge } from '../components/ui/StatusBadge'
import { Skeleton } from '../components/ui/Skeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorState } from '../components/ui/ErrorState'
import { incidentRef } from '../lib/format'

function todayIsoDate(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function Reports() {
  const [date, setDate] = useState(todayIsoDate)
  const [report, setReport] = useState<DailyReportOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setErr(null)
    setCopied(false)
    api
      .get<DailyReportOut>(`/api/reports/daily?date=${date}`)
      .then((r) => alive && setReport(r))
      .catch((e) => alive && setErr(errText(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [date])

  const copy = () => {
    if (!report) return
    navigator.clipboard.writeText(report.slack_markdown).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="h-full overflow-y-auto px-4 pb-10 md:px-8">
      <div className="animate-in mx-auto max-w-3xl space-y-5 pt-2">
        <div className="flex items-center justify-between gap-3">
          <label className="flex items-center gap-2 text-sm font-medium text-ink-2">
            Date
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded-xl border border-hair bg-surface-2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            />
          </label>
        </div>

        {err ? (
          <ErrorState detail={err} />
        ) : loading || !report ? (
          <div className="space-y-3">
            <Skeleton className="h-24 rounded-2xl" />
            <Skeleton className="h-56 rounded-2xl" />
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              {Object.entries(report.counts_by_severity).map(([sev, n]) => (
                <Badge key={sev} tone="neutral">
                  {sev}: {n}
                </Badge>
              ))}
              {report.incidents.length === 0 && <Badge tone="success">No incidents today</Badge>}
            </div>

            <Card className="p-5">
              <CardHeader
                title="Slack digest"
                action={
                  <Button variant="ghost" onClick={copy}>
                    {copied ? <Check size={15} /> : <Clipboard size={15} />}
                    {copied ? 'Copied' : 'Copy for Slack'}
                  </Button>
                }
              />
              <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-surface-2 p-4 text-sm leading-relaxed text-ink">
                {report.slack_markdown}
              </pre>
            </Card>

            <Card className="p-5">
              <div className="flex items-center gap-2">
                <ListChecks size={15} className="text-accent" />
                <h3 className="font-display text-sm font-bold text-ink">Incidents</h3>
                <Badge tone="accent">{report.incidents.length}</Badge>
              </div>
              {report.incidents.length === 0 ? (
                <EmptyState
                  icon={FileText}
                  title="Nothing to report"
                  hint="No incidents were created on this date."
                  className="mt-3 border-0 py-8"
                />
              ) : (
                <ul className="mt-3 space-y-1.5">
                  {report.incidents.map((i) => (
                    <li
                      key={i.id}
                      className="flex flex-wrap items-center gap-2.5 rounded-lg bg-surface-2 px-3 py-2 text-sm"
                    >
                      <span className="font-mono text-xs text-muted">{incidentRef(i.id)}</span>
                      <span className="font-medium text-ink">{i.service}</span>
                      <SeverityBadge severity={i.severity} size="xs" />
                      <StatusBadge status={i.status} />
                      <span className="flex-1 truncate text-ink-2">{i.summary}</span>
                      {i.ticket_url && (
                        <a
                          href={i.ticket_url}
                          target="_blank"
                          rel="noreferrer"
                          className="shrink-0 text-xs font-semibold text-accent hover:text-accent-strong"
                        >
                          ticket ↗
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
