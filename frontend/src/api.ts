import { accessToken, AuthExpiredError, expireSession } from './auth'

export type EventItem = { id: number; type: string; created_at: string; data: Record<string, any> }
export type Source = { id: string; kind: 'web'|'local'; title: string; url: string; text: string; access: string; locator: string; published_at: string; retrieved_at: string; domain?: string; evidence_level?: string; trust_label?: string }
export type Run = { id: string; thread_id: string; topic: string; status: string; report: string; sources: Source[]; error: string; created_at: string; validation: Record<string, any>; usage: Record<string, number> }
export const terminal = (status: string) => ['completed','insufficient','failed','cancelled','interrupted'].includes(status)
export function headers(): Record<string,string> {
  const h: Record<string,string> = {}
  const token = sessionStorage.getItem('dr-token')
  if(token) h.Authorization = `Bearer ${token}`
  else {
    const workspace = localStorage.getItem('dr-user')
    if(workspace) h['X-Workspace-ID'] = workspace
  }
  return h
}
export async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string | null) => {
    const requestHeaders = new Headers(init.headers)
    if (token) requestHeaders.set('Authorization', `Bearer ${token}`)
    else for (const [key, value] of Object.entries(headers())) requestHeaders.set(key, value)
    return fetch(path, {...init, headers: requestHeaders})
  }
  const token = await accessToken()
  let response = await send(token)
  if (response.status === 401) {
    const current = sessionStorage.getItem('dr-token')
    const refreshed = current && current !== token ? current : await accessToken(true)
    if (refreshed && refreshed !== token) {
      await response.body?.cancel()
      response = await send(refreshed)
    }
    if (response.status === 401) expireSession()
  }
  return response
}
export async function api<T=any>(path: string, init: RequestInit = {}): Promise<T> {
  const requestHeaders = new Headers(init.headers)
  if (!(init.body instanceof FormData) && !requestHeaders.has('Content-Type')) requestHeaders.set('Content-Type', 'application/json')
  const response = await authenticatedFetch(path, {...init, headers: requestHeaders})
  if(!response.ok) {
    const body = await response.json().catch(()=>({detail:'服务暂时不可用'}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `请求参数无效（${response.status}）`)
  }
  return response.json()
}
export function parseFrame(frame: string): EventItem | 'close' | null {
  const lines = frame.replace(/\r/g,'').split('\n')
  if(lines.some(line=>line==='event: close')) return 'close'
  const data = lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n')
  if(!data) return null
  return JSON.parse(data)
}
export async function subscribe(runId: string, onEvent: (event: EventItem)=>void, onEnd: ()=>Promise<void>, signal: AbortSignal) {
  let cursor = 0
  while(!signal.aborted) {
    try {
      const response = await authenticatedFetch(`/api/research/runs/${runId}/events?after=${cursor}`, {signal})
      if(!response.ok || !response.body) throw new Error(`进度连接失败（${response.status}）`)
      const reader = response.body.getReader(), decoder = new TextDecoder()
      let buffer = ''
      try {
        while(!signal.aborted) {
          const {value,done} = await reader.read()
          if(done) break
          buffer += decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n')
          let index: number
          while((index = buffer.indexOf('\n\n')) >= 0) {
            const frame = parseFrame(buffer.slice(0,index)); buffer = buffer.slice(index+2)
            if(frame === 'close') { await onEnd(); return }
            if(frame && frame.id > cursor) { cursor = frame.id; onEvent(frame) }
          }
        }
      } finally { await reader.cancel().catch(()=>{}) }
      await onEnd()
    } catch(error) {
      if(signal.aborted) return
      if(error instanceof AuthExpiredError) throw error
      const run = await api<Run>(`/api/research/runs/${runId}`).catch(()=>null)
      if(run && terminal(run.status)) { await onEnd(); return }
      if(error instanceof Error && /401|403|404/.test(error.message)) throw error
    }
    await new Promise<void>(resolve=>{
      const timer = setTimeout(done,1500)
      function done(){clearTimeout(timer);signal.removeEventListener('abort',done);resolve()}
      signal.addEventListener('abort',done,{once:true})
    })
  }
}
