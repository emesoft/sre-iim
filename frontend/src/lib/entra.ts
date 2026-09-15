import type { AuthenticationResult, Configuration, PublicClientApplication } from '@azure/msal-browser'
import { api } from './api'
import type { EntraConfig, LoginResponse } from './types'

/**
 * Microsoft Entra ID sign-in.
 *
 * The browser only ever obtains an **ID token** and hands it straight to our backend, which
 * verifies it and issues this application's own JWT (see `application/auth/entra_login.py`). The
 * Entra token is not kept and is never used to call our API — every authorization decision stays
 * with our backend and its roles, so Entra answers "who are you" and nothing else.
 *
 * Authorization Code + PKCE, no client secret: a secret can't be kept secret in a browser, and
 * Microsoft doesn't accept one for the SPA platform anyway.
 */

let configPromise: Promise<EntraConfig> | null = null

/** Cached because the sign-in screen renders before the button is clicked and again after. */
export function loadEntraConfig(): Promise<EntraConfig> {
  configPromise ??= api
    .get<EntraConfig>('/api/auth/entra/config')
    // A backend too old to know this route (or momentarily down) simply means "no SSO button",
    // never a broken login screen — password sign-in has to keep working regardless.
    .catch(() => ({ enabled: false, tenant_id: '', client_id: '' }))
  return configPromise
}

let msalPromise: Promise<PublicClientApplication> | null = null

function msalFor(config: EntraConfig): Promise<PublicClientApplication> {
  msalPromise ??= (async () => {
    const options: Configuration = {
      auth: {
        clientId: config.client_id,
        authority: `https://login.microsoftonline.com/${config.tenant_id}`,
        redirectUri: `${window.location.origin}/auth/callback`,
      },
      // Session storage, not local: the Entra token is a one-time step on the way to our own
      // token, so there's no reason for it to outlive the tab.
      cache: { cacheLocation: 'sessionStorage' },
    }
    // Loaded on demand: MSAL is ~250 kB and most sessions never touch it (password sign-in, or
    // an already-valid token), so it stays out of the initial bundle everyone downloads.
    const { PublicClientApplication } = await import('@azure/msal-browser')
    const instance = new PublicClientApplication(options)
    await instance.initialize()
    return instance
  })()
  return msalPromise
}

/** Runs the Microsoft popup, then exchanges the ID token for our own session. */
export async function signInWithEntra(): Promise<LoginResponse> {
  const config = await loadEntraConfig()
  if (!config.enabled) throw new Error('Microsoft sign-in is not configured on this server.')

  const instance = await msalFor(config)
  const result: AuthenticationResult = await instance.loginPopup({
    // `openid`/`profile` are what produce the ID token and its name/username claims. No Graph
    // scopes: this app reads nothing from Microsoft beyond who signed in.
    scopes: ['openid', 'profile', 'email'],
    prompt: 'select_account',
  })
  if (!result.idToken) throw new Error('Microsoft did not return an ID token.')
  return api.post<LoginResponse>('/api/auth/entra', { id_token: result.idToken })
}
