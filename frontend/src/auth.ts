export type AuthUser = { id: string; name: string; email?: string }

let issuer = import.meta.env.VITE_OIDC_ISSUER || 'http://localhost:8180/realms/deepresearch'
let validateIssuer = false
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || 'deepresearch-web'
const redirectUri = () => `${window.location.origin}/`
const tokenKey = 'dr-token'
const idTokenKey = 'dr-id-token'
const refreshTokenKey = 'dr-refresh-token'
let refreshInFlight: Promise<string | null> | null = null
const verifierKey = 'dr-pkce-verifier'
const stateKey = 'dr-pkce-state'

function base64Url(bytes: Uint8Array) {
  let value = ''
  bytes.forEach((byte) => { value += String.fromCharCode(byte) })
  return btoa(value).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '')
}
function randomValue() { const bytes = new Uint8Array(32); crypto.getRandomValues(bytes); return base64Url(bytes) }
async function challenge(verifier: string) { return base64Url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))) }
function decodeClaims(token: string): Record<string, unknown> | null { try { const payload = token.split('.')[1]; return JSON.parse(atob(payload.replaceAll('-', '+').replaceAll('_', '/'))) } catch { return null } }

export function currentUser(): AuthUser | null {
  const token = sessionStorage.getItem(tokenKey)
  if (!token) return null
  const claims = decodeClaims(token), expiresAt = Number(claims?.exp || 0) * 1000
  if (!claims?.sub || expiresAt <= Date.now() || (validateIssuer && claims.iss !== issuer)) return null
  return { id: String(claims.sub), name: String(claims.preferred_username || claims.name || claims.sub), email: typeof claims.email === 'string' ? claims.email : undefined }
}

export function configureIssuer(value?: string) {
  if (value) issuer = value.replace(/\/$/, '')
}

export class AuthExpiredError extends Error {
  constructor() { super('登录已过期，请重新登录') }
}
export function expireSession(): never {
  sessionStorage.removeItem(tokenKey); sessionStorage.removeItem(idTokenKey); sessionStorage.removeItem(refreshTokenKey)
  window.dispatchEvent(new Event('deepresearch-auth-expired'))
  throw new AuthExpiredError()
}
type TokenResponse = { access_token: string; id_token?: string; refresh_token?: string }
function saveTokens(body: TokenResponse) {
  sessionStorage.setItem(tokenKey, body.access_token)
  if (body.id_token) sessionStorage.setItem(idTokenKey, body.id_token)
  if (body.refresh_token) sessionStorage.setItem(refreshTokenKey, body.refresh_token)
}
export async function accessToken(force = false): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight
  const token = sessionStorage.getItem(tokenKey)
  const expiresAt = Number(token && decodeClaims(token)?.exp || 0) * 1000
  if (!force && token && expiresAt > Date.now() + 30000) return token
  const refreshToken = sessionStorage.getItem(refreshTokenKey)
  if (!refreshToken) return token
  refreshInFlight = (async () => {
    const response = await fetch(`${issuer}/protocol/openid-connect/token`, {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'refresh_token', client_id: clientId, refresh_token: refreshToken }),
      signal: AbortSignal.timeout(15000),
    })
    // A logout or account switch while refreshing must not resurrect the old session.
    if (sessionStorage.getItem(refreshTokenKey) !== refreshToken) throw new AuthExpiredError()
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      if (body.error === 'invalid_grant') expireSession()
      throw new Error('登录续期暂时失败，请稍后重试')
    }
    const body = await response.json() as TokenResponse
    if (sessionStorage.getItem(refreshTokenKey) !== refreshToken) throw new AuthExpiredError()
    if (!body.access_token) throw new Error('登录续期返回无效令牌')
    saveTokens(body)
    return body.access_token
  })()
  try { return await refreshInFlight } finally { refreshInFlight = null }
}

async function startAuthorization(action?: 'register') {
  const verifier = randomValue(), state = randomValue()
  sessionStorage.setItem(verifierKey, verifier); sessionStorage.setItem(stateKey, state)
  const url = new URL(`${issuer}/protocol/openid-connect/auth`)
  url.search = new URLSearchParams({ client_id: clientId, response_type: 'code', redirect_uri: redirectUri(), scope: 'openid profile email', state, code_challenge: await challenge(verifier), code_challenge_method: 'S256' }).toString()
  if (action === 'register') url.searchParams.set('prompt', 'create')
  window.location.assign(url.toString())
}
export async function login() { await startAuthorization() }
export async function register() { await startAuthorization('register') }

export async function completeLogin() {
  const params = new URLSearchParams(window.location.search), code = params.get('code')
  if (!code) { await accessToken(); return currentUser() }
  const state = params.get('state'), expectedState = sessionStorage.getItem(stateKey), verifier = sessionStorage.getItem(verifierKey)
  if (!state || state !== expectedState || !verifier) throw new Error('登录状态校验失败，请重新登录')
  const response = await fetch(`${issuer}/protocol/openid-connect/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId, code, redirect_uri: redirectUri(), code_verifier: verifier }) })
  if (!response.ok) throw new Error('Keycloak 未能完成登录令牌交换')
  const body = await response.json() as TokenResponse
  sessionStorage.removeItem(refreshTokenKey)
  saveTokens(body)
  sessionStorage.removeItem(verifierKey); sessionStorage.removeItem(stateKey)
  history.replaceState({}, document.title, window.location.pathname)
  return currentUser()
}
export function logout() {
  const idToken = sessionStorage.getItem(idTokenKey)
  sessionStorage.removeItem(refreshTokenKey)
  sessionStorage.removeItem(tokenKey); sessionStorage.removeItem(idTokenKey); sessionStorage.removeItem(verifierKey); sessionStorage.removeItem(stateKey)
  const url = new URL(`${issuer}/protocol/openid-connect/logout`)
  const params = new URLSearchParams({ client_id: clientId, post_logout_redirect_uri: redirectUri() })
  if (idToken) params.set('id_token_hint', idToken)
  url.search = params.toString()
  window.location.assign(url.toString())
}
