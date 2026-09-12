import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { accessToken, AuthExpiredError } from './auth'
import { authenticatedFetch } from './api'

const jwt = (seconds: number) => `a.${btoa(JSON.stringify({sub: 'alice', exp: Math.floor(Date.now()/1000)+seconds}))}.b`
function storage() {
  const values = new Map<string, string>()
  return {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)}
}
beforeEach(() => {
  vi.stubGlobal('sessionStorage', storage())
  vi.stubGlobal('localStorage', storage())
  vi.stubGlobal('window', {dispatchEvent: vi.fn()})
  sessionStorage.setItem('dr-token', jwt(-10))
  sessionStorage.setItem('dr-refresh-token', 'refresh-old')
})
afterEach(() => vi.unstubAllGlobals())
it('shares refresh among concurrent requests and stores rotated tokens', async () => {
  const fresh = jwt(300)
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({access_token: fresh, refresh_token: 'refresh-new'})))
  vi.stubGlobal('fetch', fetcher)
  expect(await Promise.all([accessToken(), accessToken(), accessToken()])).toEqual([fresh, fresh, fresh])
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem('dr-refresh-token')).toBe('refresh-new')
})
it('keeps credentials on temporary refresh failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', {status: 503})))
  await expect(accessToken()).rejects.toThrow('暂时失败')
  expect(sessionStorage.getItem('dr-refresh-token')).toBe('refresh-old')
  expect(window.dispatchEvent).not.toHaveBeenCalled()
})
it('clears credentials only when refresh is rejected as invalid', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":"invalid_grant"}', {status: 400})))
  await expect(accessToken()).rejects.toBeInstanceOf(AuthExpiredError)
  expect(sessionStorage.getItem('dr-refresh-token')).toBeNull()
  expect(window.dispatchEvent).toHaveBeenCalledTimes(1)
})
it('retries a 401 once using the refreshed token', async () => {
  sessionStorage.setItem('dr-token', jwt(100))
  const fresh = jwt(500)
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('', {status: 401}))
    .mockResolvedValueOnce(new Response(JSON.stringify({access_token: fresh})))
    .mockResolvedValueOnce(new Response('{}'))
  vi.stubGlobal('fetch', fetcher)
  expect((await authenticatedFetch('/api/status')).status).toBe(200)
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(fetcher.mock.calls[2][1].headers.get('Authorization')).toBe(`Bearer ${fresh}`)
})
it('does not restore credentials after logout during refresh', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => {
    sessionStorage.removeItem('dr-refresh-token')
    sessionStorage.removeItem('dr-token')
    return new Response(JSON.stringify({access_token: jwt(300)}))
  }))
  await expect(accessToken()).rejects.toBeInstanceOf(AuthExpiredError)
  expect(sessionStorage.getItem('dr-token')).toBeNull()
})
it('does not retry or clear login on authorization denial', async () => {
  sessionStorage.setItem('dr-token', jwt(300))
  const fetcher = vi.fn().mockResolvedValue(new Response('', {status: 403}))
  vi.stubGlobal('fetch', fetcher)
  expect((await authenticatedFetch('/api/admin')).status).toBe(403)
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(window.dispatchEvent).not.toHaveBeenCalled()
})
it('stops after one retry when a refreshed token is still rejected', async () => {
  sessionStorage.setItem('dr-token', jwt(100))
  const fetcher = vi.fn().mockResolvedValueOnce(new Response('', {status: 401}))
    .mockResolvedValueOnce(new Response(JSON.stringify({access_token: jwt(500)})))
    .mockResolvedValueOnce(new Response('', {status: 401}))
  vi.stubGlobal('fetch', fetcher)
  await expect(authenticatedFetch('/api/status')).rejects.toBeInstanceOf(AuthExpiredError)
  expect(fetcher).toHaveBeenCalledTimes(3)
})
it('refreshes expired login before opening the event stream', async () => {
  const { subscribe } = await import('./api')
  const fresh = jwt(300)
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({access_token: fresh})))
    .mockResolvedValueOnce(new Response('data: {"id":1,"type":"running","data":{}}\n\nevent: close\ndata: {}\n\n'))
  vi.stubGlobal('fetch', fetcher)
  const event = vi.fn(), end = vi.fn().mockResolvedValue(undefined)
  await subscribe('run', event, end, new AbortController().signal)
  expect(fetcher.mock.calls[1][1].headers.get('Authorization')).toBe(`Bearer ${fresh}`)
  expect(event).toHaveBeenCalledTimes(1)
  expect(end).toHaveBeenCalledTimes(1)
})
