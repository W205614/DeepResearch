import { accessToken, expireSession } from './auth'

export type EventItem = { id: number; type: string; created_at: string; data: Record<string, any> }
export type Source = { id: string; kind: 'web'|'local'|'attachment'; attachment_id?:string; title: string; url: string; text: string; access: string; locator: string; published_at: string; retrieved_at: string; domain?: string; evidence_level?: string; trust_label?: string; original_available?: boolean|null }
export type Publication = { id:string; source_run_id:string; topic:string; author_subject:string; report_markdown:string; sources:Source[]; validation:Record<string,any>; quality?:string; content_sha256:string; status:'pending'|'published'|'rejected'|'withdrawn'; submitted_at:string; reviewed_by:string; reviewed_at:string; review_reason:string; withdrawn_by:string; withdrawn_at:string; withdrawal_reason:string; series_id?:string; parent_report_id?:string|null; revision_no?:number; corpus_revision?:number; current_corpus_revision?:number; evidence_status?:string; is_current?:boolean; artifacts?:{requirements?:string[];claims?:{id:string;text:string}[]} }
export type Run = { created_by?:string; report_submitted?:boolean; publication?: {id:string;status:string;review_reason?:string;withdrawal_reason?:string}|null; can_resume?: boolean; error_info?: {code:string;retryable:boolean;action:string}; recovery?: {auto_recoveries:number;deadline_at:number;call_attempts:number;reserved_tokens:number}; attachments?: {id:string;name:string;media_type:string;size:number}[]; id: string; thread_id: string; topic: string; status: string; report: string; sources: Source[]; error: string; created_at: string; validation: Record<string, any>; usage: Record<string, number> }
export const terminal = (status: string) => ['completed','insufficient','failed','cancelled','interrupted'].includes(status)
export function headers(): Record<string,string> {
  const h: Record<string,string> = {}
  const token = sessionStorage.getItem('dr-token')
  if(token) h.Authorization = `Bearer ${token}`
  const workspace = localStorage.getItem('dr-workspace') || (!token ? localStorage.getItem('dr-user') : null)
  if(workspace) h['X-Workspace-ID'] = workspace
  return h
}
export async function authenticatedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string | null) => {
    const requestHeaders = new Headers(init.headers)
    if (token) requestHeaders.set('Authorization', `Bearer ${token}`)
    for (const [key, value] of Object.entries(headers())) {
      if (key === 'X-Workspace-ID' && requestHeaders.has(key)) continue
      if (key !== 'Authorization' || !token) requestHeaders.set(key, value)
    }
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
    throw new Error(typeof body.detail === 'string' ? body.detail : typeof body.message === 'string' ? body.message : `请求参数无效（${response.status}）`)
  }
  if(response.status===204)return undefined as T
  return response.json()
}
export async function apiPage<T>(path:string, cursor?:string):Promise<{items:T[];next:string}> {
  const query = new URLSearchParams({limit:'20', ...(cursor ? {cursor} : {})})
  const response = await authenticatedFetch(`${path}?${query}`)
  if (!response.ok) {
    const body = await response.json().catch(()=>({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : typeof body.message === 'string' ? body.message : `读取列表失败（${response.status}）`)
  }
  return {items:await response.json(), next:response.headers.get('X-Next-Cursor') || ''}
}
export function parseFrame(frame: string): EventItem | 'close' | null {
  const lines = frame.replace(/\r/g,'').split('\n')
  if(lines.some(line=>line==='event: close')) return 'close'
  const data = lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n')
  if(!data) return null
  return JSON.parse(data)
}
class StreamConnectionError extends Error {}
async function progressResponse(path: string, signal: AbortSignal): Promise<Response> {
  let response: Response
  try { response = await authenticatedFetch(path, {signal}) }
  catch (error) {
    if (!(error instanceof TypeError)) throw error
    throw new StreamConnectionError('进度连接暂时不可用', {cause: error})
  }
  if (!response.ok) {
    const message = `进度连接失败（${response.status}）`
    if ([429, 500, 502, 503, 504].includes(response.status)) throw new StreamConnectionError(message)
    throw new Error(message)
  }
  return response
}
export async function subscribe(runId: string, onEvent: (event: EventItem)=>void, onEnd: ()=>Promise<void>, signal: AbortSignal) {
  let cursor = 0, failures = 0
  while(!signal.aborted) {
    try {
      const response = await progressResponse(`/api/research/runs/${runId}/events?after=${cursor}`, signal)
      if(!response.body) throw new Error('进度响应缺少事件流')
      const reader = response.body.getReader(), decoder = new TextDecoder()
      let buffer = ''
      try {
        while(!signal.aborted) {
          let result: ReadableStreamReadResult<Uint8Array>
          try { result = await reader.read() }
          catch (error) {
            if (!(error instanceof TypeError)) throw error
            throw new StreamConnectionError('进度连接已断开', {cause: error})
          }
          const {value,done} = result
          if(done) break
          buffer += decoder.decode(value,{stream:true}).replace(/\r\n/g,'\n')
          let index: number
          while((index = buffer.indexOf('\n\n')) >= 0) {
            const frame = parseFrame(buffer.slice(0,index)); buffer = buffer.slice(index+2)
            if(frame === 'close') { await onEnd(); return }
            if(frame && frame.id > cursor) { onEvent(frame); cursor = frame.id; failures = 0 }
          }
        }
      } finally { await reader.cancel().catch(()=>{}) }
      await onEnd()
      throw new StreamConnectionError('进度连接已断开')
    } catch(error) {
      if(signal.aborted) return
      if(!(error instanceof StreamConnectionError)) throw error
      const status = await progressResponse(`/api/research/runs/${runId}`, signal).catch(cause=>{
        if (!(cause instanceof StreamConnectionError) && !signal.aborted) throw cause
        return null
      })
      if(signal.aborted) return
      const run: Run | null = status ? await status.json() : null
      if(run && terminal(run.status)) { await onEnd(); return }
      if(++failures >= 3) throw new Error('进度连接连续失败，请刷新页面重试', {cause: error})
    }
    await new Promise<void>(resolve=>{
      const timer = setTimeout(done,1500)
      function done(){clearTimeout(timer);signal.removeEventListener('abort',done);resolve()}
      signal.addEventListener('abort',done,{once:true})
    })
  }
}
