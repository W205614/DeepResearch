export type AuthUser = { id: string; name: string; email?: string }

const issuer = import.meta.env.VITE_OIDC_ISSUER || 'http://localhost:8180/realms/deepresearch'
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID || 'deepresearch-web'
const redirectUri = `${window.location.origin}/`
const tokenKey = 'dr-token'
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
  if (!claims?.sub || expiresAt <= Date.now()) { sessionStorage.removeItem(tokenKey); return null }
  return { id: String(claims.sub), name: String(claims.preferred_username || claims.name || claims.sub), email: typeof claims.email === 'string' ? claims.email : undefined }
}

async function startAuthorization(action?: 'register') {
  const verifier = randomValue(), state = randomValue()
  sessionStorage.setItem(verifierKey, verifier); sessionStorage.setItem(stateKey, state)
  const url = new URL(`${issuer}/protocol/openid-connect/auth`)
  url.search = new URLSearchParams({ client_id: clientId, response_type: 'code', redirect_uri: redirectUri, scope: 'openid profile email', state, code_challenge: await challenge(verifier), code_challenge_method: 'S256' }).toString()
  if (action === 'register') url.searchParams.set('kc_action', 'register')
  window.location.assign(url.toString())
}
export async function login() { await startAuthorization() }
export async function loginDevelopment() {
  const response = await fetch('/api/auth/development/login', { method: 'POST' })
  if (!response.ok) throw new Error('本地工作空间登录不可用')
  const body = await response.json() as { access_token: string }
  sessionStorage.setItem(tokenKey, body.access_token)
  return currentUser()
}
export async function register() { await startAuthorization('register') }

export async function completeLogin() {
  const params = new URLSearchParams(window.location.search), code = params.get('code')
  if (!code) return currentUser()
  const state = params.get('state'), expectedState = sessionStorage.getItem(stateKey), verifier = sessionStorage.getItem(verifierKey)
  if (!state || state !== expectedState || !verifier) throw new Error('登录状态校验失败，请重新登录')
  const response = await fetch(`${issuer}/protocol/openid-connect/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId, code, redirect_uri: redirectUri, code_verifier: verifier }) })
  if (!response.ok) throw new Error('Keycloak 未能完成登录令牌交换')
  const body = await response.json() as { access_token: string }
  sessionStorage.setItem(tokenKey, body.access_token); sessionStorage.removeItem(verifierKey); sessionStorage.removeItem(stateKey)
  history.replaceState({}, document.title, window.location.pathname)
  return currentUser()
}
export function logout() {
  sessionStorage.removeItem(tokenKey); sessionStorage.removeItem(verifierKey); sessionStorage.removeItem(stateKey)
  const url = new URL(`${issuer}/protocol/openid-connect/logout`)
  url.search = new URLSearchParams({ client_id: clientId, post_logout_redirect_uri: redirectUri }).toString()
  window.location.assign(url.toString())
}