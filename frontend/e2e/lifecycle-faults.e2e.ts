import { expect, test, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const execute=promisify(execFile)
const project='dr-lifecycle-acceptance'
const compose=['compose','-f','compose.yaml','-f','compose.acceptance.yaml','-p',project]
async function docker(...args:string[]){return execute('docker',[...compose,...args],{cwd:'..',timeout:45_000,maxBuffer:2*1024*1024})}
async function login(page:Page,username:string,password:string,workspace:string){
  await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click()
  await page.locator('#username').fill(username);await page.locator('#password').fill(password);await page.locator('#kc-login').click()
  await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:30_000})
  return{Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':workspace}
}
async function completed(page:Page,id:string,headers:Record<string,string>,timeout=120_000){
  let result:any
  await expect.poll(async()=>{const response=await page.request.get(`/api/research/runs/${id}`,{headers});expect(response.ok()).toBe(true);result=await response.json();return result.status},{timeout,intervals:[1000,2000,4000]}).toMatch(/^(completed|insufficient|failed|cancelled)$/)
  expect(result.status,JSON.stringify({error:result.error,validation:result.validation})).toBe('completed')
  return result
}

test('isolated lifecycle faults recover accepted work and enforce stale-review rejection',async({browser})=>{
  test.skip(process.env.E2E_LIFECYCLE_FAULTS!=='1','Explicit isolated fault acceptance only.')
  expect(process.env.E2E_COMPOSE_PROJECT).toBe(project)
  const base=new URL(process.env.E2E_BASE_URL||'')
  expect(['127.0.0.1','localhost']).toContain(base.hostname);expect(base.port).toBe('28080')
  test.setTimeout(600_000)
  const context=await browser.newContext(),reviewerContext=await browser.newContext()
  const page=await context.newPage(),reviewer=await reviewerContext.newPage(),tag=randomUUID().slice(0,8)
  const directory=resolve('../.cache/lifecycle-faults',`${new Date().toISOString().replaceAll(':','-')}-${tag}`);mkdirSync(directory,{recursive:true})
  const evidence={started_at:new Date().toISOString(),worker:null as unknown,redis:null as unknown,milvus:null as unknown,stale_review:null as unknown,errors:[] as string[],restored:false}
  let headers:Record<string,string>|null=null,docId='',documentVersion=0
  async function create(topic:string,policy='public'){
    const response=await page.request.post('/api/research/runs',{headers:headers!,data:{topic,mode:'deep',data_policy:policy,client_request_id:randomUUID()}})
    expect(response.status()).toBe(202);return response.json()
  }
  async function documentReady(){
    let doc:any
    await expect.poll(async()=>{const response=await page.request.get('/api/documents',{headers:headers!});expect(response.ok()).toBe(true);doc=(await response.json()).find((item:any)=>item.id===docId);return doc?.error?'failed':doc?.status==='ready'&&doc.index_version<=documentVersion?'waiting_for_new_version':doc?.status},{timeout:120_000,intervals:[1000,2000]}).toMatch(/^(ready|failed|quarantined|scan_failed)$/)
    expect(doc.status,JSON.stringify({error:doc.error,version:doc.index_version})).toBe('ready')
    expect(doc.error).toBeFalsy();expect(doc.index_version).toBeGreaterThan(documentVersion);documentVersion=doc.index_version
    return doc
  }
  async function upload(text:string){
    const response=await page.request.fetch(docId?`/api/documents/${docId}`:'/api/documents',{method:docId?'PUT':'POST',headers:headers!,multipart:{file:{name:`fault-policy-${tag}.md`,mimeType:'text/markdown',buffer:Buffer.from(text)}}})
    expect(response.status()).toBe(202);docId=(await response.json()).id;return documentReady()
  }
  try{
    expect([process.env.E2E_USERNAME,process.env.E2E_PASSWORD,process.env.E2E_AUTHOR_SUBJECT,process.env.E2E_REVIEWER_USERNAME,process.env.E2E_REVIEWER_PASSWORD].every(Boolean)).toBe(true)
    headers=await login(page,process.env.E2E_USERNAME!,process.env.E2E_PASSWORD!,process.env.E2E_AUTHOR_SUBJECT!)
    const reviewerHeaders=await login(reviewer,process.env.E2E_REVIEWER_USERNAME!,process.env.E2E_REVIEWER_PASSWORD!,process.env.E2E_AUTHOR_SUBJECT!)
    const invite=await page.request.put(`/api/workspaces/${process.env.E2E_AUTHOR_SUBJECT}/members`,{headers,data:{subject:process.env.E2E_REVIEWER_SUBJECT,role:'admin'}});expect(invite.ok()).toBe(true)

    const interrupted=await create(`slow-recovery fault-${tag} 如何组织研究流程？`)
    await expect.poll(async()=>{const log=await docker('logs','--tail','100','worker');return log.stdout.includes(`run=${interrupted.id.slice(0,8)} node=planner phase=start`)},{timeout:30_000,intervals:[500,1000]}).toBe(true)
    const killedAt=Date.now();await docker('kill','-s','SIGKILL','worker');await docker('start','worker')
    const recovered=await completed(page,interrupted.id,headers,120_000)
    expect(Number(recovered.attempt_count)).toBe(2)
    const stream=await page.request.get(`/api/research/runs/${interrupted.id}/events`,{headers});expect(stream.ok()).toBe(true)
    const events=(await stream.text()).split('\n').filter(line=>/^data:\s*\{/.test(line)).map(line=>JSON.parse(line.slice(5).trim()))
    expect(events.filter(event=>event.type==='done')).toHaveLength(1)
    evidence.worker={run_id:interrupted.id,status:recovered.status,attempt_count:recovered.attempt_count,done_events:1,recovered_ms:Date.now()-killedAt}
    writeFileSync(resolve(directory,'progress.json'),JSON.stringify(evidence,null,2))

    await docker('stop','-t','1','redis')
    const acceptedAt=Date.now(),queued=await create(`redis-outbox fault-${tag} 如何组织研究流程？`)
    expect(queued.status).toBe('queued')
    await docker('start','redis')
    const delivered=await completed(page,queued.id,headers,120_000)
    evidence.redis={run_id:queued.id,accepted_status:queued.status,final_status:delivered.status,recovered_ms:Date.now()-acceptedAt}
    writeFileSync(resolve(directory,'progress.json'),JSON.stringify(evidence,null,2))

    await upload('# 报销制度\n\n正式员工报销上限为 500 元。实习生不适用本制度。\n')
    await docker('stop','-t','1','milvus')
    const readiness=await page.request.get('/readyz');expect(readiness.status()).toBe(200)
    const degraded=await page.request.post('/api/documents/search?diagnostics=true',{headers,data:{query:`正式员工报销上限 fault-${tag}`,limit:6}})
    expect(degraded.ok()).toBe(true);const retrieval=await degraded.json()
    expect(retrieval.reasons).toContain('vector_unavailable');expect(retrieval.results.some((item:any)=>item.document_id===docId)).toBe(true)
    await docker('start','milvus')
    await expect.poll(async()=>{const response=await page.request.get('/api/capabilities',{headers:headers!});expect(response.ok()).toBe(true);return (await response.json()).vector_search},{timeout:120_000,intervals:[2000,4000]}).toBe(true)
    evidence.milvus={ready_status:readiness.status(),fallback_reasons:retrieval.reasons,results:retrieval.results.length,recovered:true}
    writeFileSync(resolve(directory,'progress.json'),JSON.stringify(evidence,null,2))

    const internal=await create(`根据 fault-policy-${tag}.md，正式员工报销上限是多少？实习生是否适用？`,'internal')
    await completed(page,internal.id,headers)
    const submission=await page.request.post(`/api/research/runs/${internal.id}/publication`,{headers});expect(submission.status()).toBe(201)
    const report=await submission.json()
    await upload('# 报销制度\n\n正式员工报销上限为 800 元。实习生仍不适用本制度。\n')
    const stale=await reviewer.request.post(`/api/reports/${report.id}/approve`,{headers:reviewerHeaders,data:{reason:'并发资料更新后的过期审核应拒绝'}})
    expect(stale.status()).toBe(409)
    const unchanged=await page.request.get(`/api/reports/${report.id}`,{headers});expect((await unchanged.json()).status).toBe('pending')
    evidence.stale_review={report_id:report.id,approval_status:stale.status(),report_status:'pending'}
  }catch(error){evidence.errors.push(error instanceof Error?error.message:String(error));throw error}
  finally{
    try{await docker('start','redis','milvus','worker');evidence.restored=true}catch(error){evidence.errors.push(`restore: ${error instanceof Error?error.message:String(error)}`)}
    writeFileSync(resolve(directory,'summary.json'),JSON.stringify(evidence,null,2))
    console.log(`Fault evidence: ${directory}`)
    await Promise.all([context.close(),reviewerContext.close()])
    expect(evidence.restored,'Faulted services must be restored').toBe(true)
  }
})
