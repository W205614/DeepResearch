import { beforeEach, afterEach, expect, it, vi } from 'vitest'

const issuer = 'https://identity.company.test/tenant'
const metadata = {
  issuer, authorization_endpoint: 'https://login.company.test/authorize?tenant=research',
  token_endpoint: 'https://identity.company.test/oauth/token',
  end_session_endpoint: 'https://login.company.test/session/end',
  code_challenge_methods_supported: ['S256'],
}
const configuration = { mode: 'oidc', issuer, client_id: 'corporate-spa', registration_enabled: false }
const jwt = (iss = issuer, seconds = 300) => `a.${btoa(JSON.stringify({ sub: 'alice', iss, exp: Math.floor(Date.now()/1000)+seconds }))}.b`

beforeEach(() => {
  vi.resetModules()
  const values = new Map<string, string>()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
  vi.stubGlobal('window', { location: { origin: 'https://research.company.test', assign: vi.fn() }, dispatchEvent: vi.fn() })
})
afterEach(() => vi.unstubAllGlobals())

it('discovers generic OIDC endpoints and uses the runtime client, scopes and PKCE', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(metadata)))
  vi.stubGlobal('fetch', fetcher)
  const auth = await import('./auth')
  await auth.configureAuth({ ...configuration, scopes: 'openid profile offline_access api.read' })
  await auth.login()
  expect(fetcher.mock.calls[0][0]).toBe(`${issuer}/.well-known/openid-configuration`)
  const url = new URL(vi.mocked(window.location.assign).mock.calls[0][0] as string)
  expect(url.origin + url.pathname).toBe('https://login.company.test/authorize')
  expect(Object.fromEntries(url.searchParams)).toMatchObject({
    tenant: 'research', client_id: 'corporate-spa', scope: 'openid profile offline_access api.read',
    response_type: 'code', redirect_uri: 'https://research.company.test/', code_challenge_method: 'S256',
  })
  expect(url.searchParams.get('code_challenge')).toHaveLength(43)
  expect(url.searchParams.get('state')).toBe(sessionStorage.getItem('dr-pkce-state'))
  await expect(auth.register()).rejects.toThrow('组织管理员')
})

it('refreshes through the discovered token endpoint and uses RP logout', async () => {
  const fresh = jwt()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(metadata)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: fresh, refresh_token: 'rotated' }))))
  const auth = await import('./auth')
  await auth.configureAuth(configuration)
  sessionStorage.setItem('dr-token', jwt(issuer, -10)); sessionStorage.setItem('dr-refresh-token', 'old')
  expect(await auth.accessToken()).toBe(fresh)
  const [url, options] = vi.mocked(fetch).mock.calls[1]
  expect(url).toBe(metadata.token_endpoint)
  expect(String(options?.body)).toContain('client_id=corporate-spa')
  expect(auth.currentUser()?.id).toBe('alice')
  auth.logout()
  expect(sessionStorage.getItem('dr-token')).toBeNull()
  const target = new URL(vi.mocked(window.location.assign).mock.calls[0][0] as string)
  expect(target.origin + target.pathname).toBe(metadata.end_session_endpoint)
})

it('clears a previous issuer session before it can send refresh credentials to a new provider', async () => {
  sessionStorage.setItem('dr-token', jwt('https://old.company.test'))
  sessionStorage.setItem('dr-refresh-token', 'old-provider-refresh')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(metadata))))
  const auth = await import('./auth')
  await auth.configureAuth(configuration)
  expect(sessionStorage.getItem('dr-refresh-token')).toBeNull()
  expect(auth.currentUser()).toBeNull()
})

it.each([
  { ...metadata, issuer: 'https://other.company.test' },
  { ...metadata, token_endpoint: 'http://identity.company.test/token' },
  { ...metadata, authorization_endpoint: 'https://secret@identity.company.test/auth' },
  { ...metadata, code_challenge_methods_supported: ['plain'] },
])('rejects mismatched discovery or unsafe endpoints', async value => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(value))))
  const auth = await import('./auth')
  await expect(auth.configureAuth(configuration)).rejects.toThrow()
})

it('rejects a token from another issuer without saving it', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(metadata)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: jwt('https://other.company.test') }))))
  const auth = await import('./auth')
  await auth.configureAuth(configuration)
  const old = jwt(issuer, -10)
  sessionStorage.setItem('dr-token', old); sessionStorage.setItem('dr-refresh-token', 'old')
  await expect(auth.accessToken()).rejects.toThrow('登录配置不一致')
  expect(sessionStorage.getItem('dr-token')).toBe(old)
})

it('supports providers without an RP logout endpoint by clearing the local session', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...metadata, end_session_endpoint: undefined }))))
  const auth = await import('./auth')
  await auth.configureAuth(configuration)
  sessionStorage.setItem('dr-token', jwt())
  auth.logout()
  expect(window.location.assign).toHaveBeenCalledWith('https://research.company.test/')
  expect(sessionStorage.getItem('dr-token')).toBeNull()
})
