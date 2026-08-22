// Thin fetch wrapper. Talks to the backend via the relative `/api` (and `/healthz`)
// paths, which Vite proxies to :8000 in dev. Non-2xx responses throw an ApiError
// carrying the backend's `detail` string so the UI can surface 422 validation messages.

const ADMIN_TOKEN_STORAGE_KEY = 'iim_admin_token'

export function getAdminToken(): string | null {
  try {
    return localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
  } catch {
    return null // private-browsing / storage blocked — treat as logged out
  }
}

export function setAdminToken(token: string): void {
  try {
    localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token)
  } catch {
    // storage blocked — nothing we can do, the gate will just re-prompt next load
  }
}

export function clearAdminToken(): void {
  try {
    localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
  } catch {
    // ignore
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`)
    this.name = 'ApiError'
  }
}

function authHeaders(): Record<string, string> {
  const token = getAdminToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null // non-JSON error page (e.g. proxy 500) — fall back to status text
  }
  if (!res.ok) {
    if (res.status === 401) clearAdminToken() // stale/expired admin session — force re-login
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? typeof (body as { detail: unknown }).detail === 'string'
          ? (body as { detail: string }).detail
          : JSON.stringify((body as { detail: unknown }).detail)
        : res.statusText || `Request failed (HTTP ${res.status})`
    throw new ApiError(res.status, detail)
  }
  return body as T
}

/** Normalise any thrown value (ApiError, network TypeError, …) to a message. */
export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail
  if (e instanceof Error) return e.message
  return String(e)
}

/**
 * True only when the request never got a response at all (the backend process is down/
 * unreachable — a network-level `fetch` failure). An `ApiError` means the backend *did*
 * respond, just with a non-2xx status (e.g. a 500 from a downstream failure) — a different
 * problem with a different fix, so the UI shouldn't tell the user to go start the backend.
 */
export function isUnreachable(e: unknown): boolean {
  return !(e instanceof ApiError)
}

export const api = {
  get: <T>(path: string): Promise<T> =>
    fetch(path, { headers: authHeaders() }).then((r) => handle<T>(r)),
  post: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  del: <T>(path: string): Promise<T> =>
    fetch(path, { method: 'DELETE', headers: authHeaders() }).then((r) => handle<T>(r)),
  patch: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  put: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
}
