import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseFrame, subscribe, terminal } from './api'

beforeEach(() => {
  vi.stubGlobal('sessionStorage', {getItem: () => null})
  vi.stubGlobal('localStorage', {getItem: () => null})
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
describe('durable event stream',()=>{
  it('ignores heartbeats',()=>expect(parseFrame(': keep-alive')).toBeNull())
  it('parses unicode event data',()=>expect(parseFrame('id: 4\ndata: {"id":4,"type":"node_start","data":{"node":"证据"}}')?.valueOf()).toEqual({id:4,type:'node_start',data:{node:'证据'}}))
  it('closes explicitly',()=>expect(parseFrame('event: close\ndata: {}')).toBe('close'))
  it('recognizes interrupted and insufficient tasks as terminal',()=>{expect(terminal('interrupted')).toBe(true);expect(terminal('insufficient')).toBe(true);expect(terminal('running')).toBe(false)})
})

describe('progress error handling', () => {
  it('reports malformed event JSON without reconnecting', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('data: {broken}\n\n'))
    vi.stubGlobal('fetch', fetcher)
    await expect(subscribe('run', vi.fn(), vi.fn(), new AbortController().signal)).rejects.toBeInstanceOf(SyntaxError)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('propagates event and completion callback failures', async () => {
    const defect = new TypeError('render defect')
    const fetcher = vi.fn().mockImplementation(async () => new Response('data: {"id":1}\n\nevent: close\n\n'))
    vi.stubGlobal('fetch', fetcher)
    await expect(subscribe('run', () => {throw defect}, vi.fn(), new AbortController().signal)).rejects.toBe(defect)
    await expect(subscribe('run', vi.fn(), async () => {throw defect}, new AbortController().signal)).rejects.toBe(defect)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('does not retry authorization failures', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('', {status:403}))
    vi.stubGlobal('fetch', fetcher)
    await expect(subscribe('run', vi.fn(), vi.fn(), new AbortController().signal)).rejects.toThrow('403')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('reconnects after a temporary network failure and delivers the next stream', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce(new Response('{"status":"running"}'))
      .mockResolvedValueOnce(new Response('data: {"id":1,"type":"done","data":{}}\n\nevent: close\n\n'))
    vi.stubGlobal('fetch', fetcher)
    const event = vi.fn(), end = vi.fn()
    const result = subscribe('run', event, end, new AbortController().signal)
    await vi.runAllTimersAsync()
    await result
    expect(fetcher).toHaveBeenCalledTimes(3)
    expect(event).toHaveBeenCalledTimes(1)
    expect(end).toHaveBeenCalledTimes(1)
  })
  it('reports persistent transport failure after three attempts', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn().mockImplementation(async (path: string) => {
      if (path.includes('/events?')) throw new TypeError('network failed')
      return new Response('{"status":"running"}')
    })
    vi.stubGlobal('fetch', fetcher)
    const assertion = expect(subscribe('run', vi.fn(), vi.fn(), new AbortController().signal)).rejects.toThrow('连续失败')
    await vi.runAllTimersAsync()
    await assertion
    expect(fetcher).toHaveBeenCalledTimes(6)
  })
})
