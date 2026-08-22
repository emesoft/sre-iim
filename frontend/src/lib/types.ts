// TypeScript mirrors of the backend response/request DTOs.
// Source of truth: backend/app/interface/http/dto/* (see .claude/specs/FRONTEND_LOCAL.md §3).

export interface EvidenceRef {
  chunk_id: string
  source_type: string
  title: string
}

export interface KnownIssueOut {
  incident_id: string
  similarity: number
}

export interface AnalysisOut {
  severity: string
  summary: string
  root_cause: string
  recommended_action: string
  confidence: number | null
  model_id: string
  _cache: 'HIT' | 'MISS'
  evidence: EvidenceRef[]
  known_issue: KnownIssueOut | null
}

export interface IncidentSummary {
  id: string
  service: string
  source: string
  status: string
  fingerprint: string
  created_at: string
  severity?: string | null
  summary?: string | null
  headline?: string | null
}

export interface IncidentDetail {
  id: string
  service: string
  source: string
  status: string
  fingerprint: string
  context: Record<string, unknown>
  created_at: string
  updated_at: string
  log_group: string | null
  ticket_url: string | null
  analysis: AnalysisOut | null
}

export interface LogEventOut {
  timestamp: string
  message: string
  /** Severity parsed from the line; null when the source line carried none. */
  level: string | null
}

export interface LogSearchResult {
  log_group: string
  log_events: LogEventOut[]
  analysis: AnalysisOut
}

export interface IncidentCreated {
  incident_id: string
  status: string
  stream: string
}

// GET /api/incidents/{id}/stream (SSE) — one message per line: `event: <type>` + `data: <json>`.
export interface StageEvent {
  stage: string
  label: string
  detail: string | null
}

export type IncidentStreamEvent =
  | { event: 'stage'; data: StageEvent }
  | { event: 'analyzed'; data: IncidentDetail }
  | { event: 'failed'; data: { message: string } }

export interface DocumentSummary {
  id: string
  title: string
  source_type: string
  service: string | null
  tags: string[]
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface DocumentCreated {
  document_id: string
  chunks: number
}

export interface Health {
  status: string
  app: string
  database: string
}

export type SourceType = 'runbook' | 'postmortem' | 'architecture' | 'vendor'
export const SOURCE_TYPES: SourceType[] = ['runbook', 'postmortem', 'architecture', 'vendor']

export interface ReportIncidentOut {
  id: string
  service: string
  status: string
  severity: string | null
  summary: string | null
  ticket_url: string | null
}

export interface DailyReportOut {
  report_date: string
  counts_by_severity: Record<string, number>
  counts_by_status: Record<string, number>
  incidents: ReportIncidentOut[]
  slack_markdown: string
}

export interface CloudConnection {
  id: string
  project: string
  env: string
  cloud: string
  region: string
  auth_type: 'sso' | 'access_key'
  sso_profile_name: string | null
  has_access_key: boolean
  last_poll_at: string | null
  last_poll_status: 'ok' | 'error' | null
  last_poll_error: string | null
  last_poll_alarm_count: number | null
  created_at: string
}

export interface CloudConnectionCreate {
  project: string
  env: string
  region: string
  auth_type: 'sso' | 'access_key'
  sso_profile_name?: string
  access_key_id?: string
  secret_access_key?: string
}

export interface TestConnectionResult {
  ok: boolean
  error: string | null
}

export interface PollResult {
  polled: number
  alarm_count: number
  errors: number
}

export interface PollSchedule {
  interval_minutes: number
  next_run_at: string | null
}

export interface SettingStatus {
  is_set: boolean
}

export interface AdminLoginResponse {
  token: string
}
