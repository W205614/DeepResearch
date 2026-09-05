export type EventItem = { id: number; type: string; created_at: string; data: Record<string, any> }
export type Source = { id: string; kind: 'web'|'local'; title: string; url: string; text: string; access: string; locator: string; published_at: string; retrieved_at: string; domain?: string; evidence_level?: string; trust_label?: string }
export type Run = { id: string; thread_id: string; topic: string; status: string; report: string; sources: Source[]; error: string; created_at: string; validation: Record<string, any>; usage: Record<string, number> }
export const terminal = (status: string) => ['completed','insufficient','failed','cancelled','interrupted'].includes(status)
export function headers(): Record<string,string> {
  const h: Record<string,string> = {'X-User-ID': localStorage.getItem('dr-user') || 'local-user'}
  const token = sessionStorage.getItem('dr-token')
  if(token) h.Authorization = `Bearer ${token}`
  return h
}
export async function api<T=any>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {...init, headers: {...headers(), ...(init.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...init.headers}})
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
      const response = await fetch(`/api/research/runs/${runId}/events?after=${cursor}`, {headers: headers(), signal})
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
