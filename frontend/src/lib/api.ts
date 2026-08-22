// Thin fetch wrapper. Talks to the backend via the relative `/api` (and `/healthz`)
// paths, which Vite proxies to :8000 in dev. Non-2xx responses throw an ApiError
// carrying the backend's `detail` string so the UI can surface 422 validation messages.

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`)
    this.name = 'ApiError'
  }
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
  get: <T>(path: string): Promise<T> => fetch(path).then((r) => handle<T>(r)),
  post: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  del: <T>(path: string): Promise<T> => fetch(path, { method: 'DELETE' }).then((r) => handle<T>(r)),
  put: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
}
