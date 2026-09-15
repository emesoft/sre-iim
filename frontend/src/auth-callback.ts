/**
 * The page Microsoft redirects the sign-in popup to.
 *
 * MSAL v5 changed how a popup returns its result. It no longer polls the popup's URL from the
 * opener; the redirect page itself has to parse the response and broadcast it back over a
 * BroadcastChannel, which is what `broadcastResponseToMainFrame` does. A page that does nothing —
 * correct for MSAL v3, and what this was — leaves the opener waiting forever: the popup sits on
 * the callback page, `loginPopup` never resolves, and the backend never even sees a request, so
 * nothing anywhere reports an error.
 *
 * It is deliberately its own entry point rather than a route in the SPA. Letting the app boot here
 * showed a second sign-in screen inside the popup, whose button threw `block_nested_popups`, and
 * made the popup download the whole bundle to do one thing.
 */
import { broadcastResponseToMainFrame } from '@azure/msal-browser/redirect-bridge'

broadcastResponseToMainFrame().catch((error: unknown) => {
  // The window stays open on failure, showing why. Closing it silently would leave the opener's
  // spinner as the only evidence, which is how the original bug stayed invisible for so long.
  const detail = error instanceof Error ? error.message : String(error)
  document.getElementById('waiting')?.setAttribute('style', 'display:none')
  const box = document.getElementById('error')
  const text = document.getElementById('detail')
  if (text) text.textContent = detail
  box?.setAttribute('style', 'display:block')
})
