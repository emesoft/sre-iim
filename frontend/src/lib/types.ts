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
  // null means "not tracked for this provider" — currently only claude_cli reports usage.
  input_tokens: number | null
  output_tokens: number | null
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
  env?: string | null
  occurrence_count: number
  previous_incident_id: string | null
}

export interface ProjectRollup {
  project: string
  open: number
  urgent: number
  untriaged: number
  top_incident_id: string | null
  top_headline: string | null
}

export interface NoisyAlarm {
  service: string
  fingerprint: string
  count: number
  label: string
}

/** `GET /api/incidents/rollup` — dashboard totals aggregated in SQL. Counted over the whole table,
 * unlike anything derived from the (paged) incident list. */
export interface IncidentRollup {
  /** Triage lane -> count. The tab counts read this, never a page of rows. */
  lanes: Record<string, number>
  /** Options for the project filter — deliberately unaffected by that filter. */
  all_projects: string[]
  active: number
  urgent: number
  untriaged: number
  new_last_24h: number
  projects: ProjectRollup[]
  noisy: NoisyAlarm[]
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
  headline: string | null
  env: string | null
  error_message: string | null
  occurrence_count: number
  previous_incident_id: string | null
}

export interface ChatMessageOut {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Fresh input only. The cached prefix — system prompt, incident context, transcript so far — is
   * separate: it is re-read every turn and costs a fraction of the price, so one combined figure
   * looks alarming and says almost nothing. */
  input_tokens: number | null
  cached_input_tokens: number | null
  output_tokens: number | null
  created_at: string
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

export interface DocumentDetail extends DocumentSummary {
  content: string
}

export interface DocumentCreated {
  document_id: string
  chunks: number
}

export interface SeedDocumentsResponse {
  seeded: number
  skipped: number
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

/** A capability an integration can provide. Mirrors domain/integrations/entities.py. */
export type Capability = 'alarms' | 'logs' | 'tickets' | 'cost'

export interface CapabilityHealth {
  capability: string
  last_run_at: string | null
  status: 'ok' | 'error' | null
  error: string | null
  item_count: number | null
}

/**
 * One external system wired to one project. Provider-specific fields live in `config`; the
 * credentials themselves never reach the browser — `secret_names` only says which are stored.
 */
export interface Integration {
  id: string
  project: string
  env: string
  provider: string
  display_name: string | null
  enabled: boolean
  config: Record<string, string>
  secret_names: string[]
  capabilities: string[]
  health: CapabilityHealth[]
  created_at: string
}

export interface IntegrationCreate {
  project: string
  env: string
  provider: string
  display_name?: string | null
  config: Record<string, string>
  secrets: Record<string, string>
  capabilities: string[]
}

/** `GET /api/providers` — the backend registry's catalog. The integration form builds its fields
 * from this, so adding a provider needs no frontend change. */
export interface Provider {
  provider: string
  label: string
  capabilities: string[]
  required_config: string[]
  secret_names: string[]
  notes: string
}

export interface TestConnectionResult {
  ok: boolean
  error: string | null
}

export interface Project {
  id: string
  name: string
  /** Whether urgent alarms in this project are triaged automatically. */
  auto_analyze: boolean
  created_at: string
}

export interface ProjectCreate {
  name: string
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

/** What a group may do. `guest` is not a group — it's what belonging to none resolves to, and no
 * endpoint accepts it. */
export type Role = 'admin' | 'sre' | 'consultant'
export type EffectiveRole = Role | 'guest'

export const ROLE_LABELS: Record<EffectiveRole, string> = {
  admin: 'Full access, manages users and integrations',
  sre: 'Investigates incidents, analyses, tickets',
  consultant: 'Read-only on the projects listed',
  guest: 'No permissions until an admin assigns a group',
}

/** A group: a permission level plus the projects it opens up. One per user, and the only thing
 * that grants anything. */
export interface Group {
  id: string
  name: string
  role: Role
  description: string | null
  /** Optional model profile this cohort's analyses bill to; null follows the deployment default. */
  model_profile_id: string | null
  /** Always empty for an admin group, which is unrestricted by role. */
  projects: string[]
  member_count: number
  created_at: string
}

export interface GroupWrite {
  name: string
  role: Role
  description?: string | null
  projects: string[]
  model_profile_id?: string | null
}

export interface UserOut {
  id: string
  username: string
  email: string | null
  /** Read off the group, never stored on the account — `guest` when there is no group. */
  role: EffectiveRole
  /** `local` accounts have a password here; `entra` ones authenticate with Microsoft and have
   * none — setting one would bypass SSO, so the backend refuses it. */
  auth_provider: 'local' | 'entra'
  group_id: string | null
  group_name: string | null
  /** Projects this user may see, via their group. Admins are unrestricted regardless. */
  projects: string[]
  created_at: string
}

export interface LoginRequest {
  username: string
  password: string
}

/** `GET /api/auth/entra/config` — both ids are public (they travel to Microsoft in the
 * authorize URL); `enabled` is false when the server has no Entra app registration configured. */
export interface EntraConfig {
  enabled: boolean
  tenant_id: string
  client_id: string
}

export interface LoginResponse {
  token: string
  user: UserOut
}

/** `POST /api/incidents/bulk` — what actually happened. `skipped` is ids the caller can no longer
 * see, reported as a count so one stale selection doesn't look like a full success. */
export interface BulkResult {
  action: string
  done: number
  skipped: number
}

export interface UsageByModel {
  model_id: string
  /** Cache reads/writes, kept apart from `input_tokens` — they cost a fraction of the price. */
  cached_input_tokens: number
  /** Recorded before the split existed: includes the cache and can't be separated now. Shown
   * apart rather than counted as fresh input. */
  unsplit_input_tokens: number
  /** `analysis` | `chat`. A missing profile means different things in each — an analysis predates
   * profiles existing, a chat message never records one. */
  source: string
  /** Which model profile paid for it — null for spend that predates profiles. */
  llm_profile: string | null
  input_tokens: number
  output_tokens: number
  analyses_count: number
}

export interface LlmUsage {
  total_input_tokens: number
  total_cached_input_tokens: number
  total_output_tokens: number
  by_model: UsageByModel[]
}

/** One input the Settings form renders for an LLM provider. The form is built from these, so a
 * provider added in the backend catalog needs no frontend change. */
export interface LlmField {
  name: string
  label: string
  secret: boolean
  required: boolean
  placeholder: string
  help: string
  /** `aws_integration` renders a picker over the configured AWS integrations. */
  source: string | null
  /** Known-good values, offered as a dropdown. Not a whitelist — "Other…" still accepts anything,
   * because a model released this morning has to be usable this morning. */
  options: string[]
}

export interface LlmProvider {
  key: string
  label: string
  notes: string
  fields: LlmField[]
  defaults: Record<string, string>
}

/** One named way to reach a model: a provider plus its settings and credential. Projects point at
 * a profile rather than carrying their own copy of a key. */
export interface LlmProfile {
  id: string
  name: string
  provider: string
  config: Record<string, string>
  /** Which credentials exist — never their values. */
  secret_names: string[]
  /** null = never tested, which is the state worth warning about. */
  last_test_ok: boolean | null
  last_test_error: string | null
}

/** `GET /api/settings/llm` */
export interface LlmSetup {
  profiles: LlmProfile[]
  default_profile_id: string | null
  /** project -> profile id, only for projects that override the default. */
  by_project: Record<string, string>
  providers: LlmProvider[]
}

/** `POST /api/integrations/aws-sso/begin` — what the operator approves, and the handle to poll
 * with. The client secret AWS issued stays on the server. */
export interface SsoBegin {
  handle: string
  verification_uri_complete: string
  user_code: string
  interval_seconds: number
  expires_in_seconds: number
}

export interface SsoRole {
  account_id: string
  account_name: string
  role_name: string
}

export interface SsoPoll {
  status: 'pending' | 'ready' | 'expired'
  roles: SsoRole[]
}
