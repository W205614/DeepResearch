import { expect, test, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const seconds=Number(process.env.E2E_SOAK_SECONDS || 0)
const pause=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms))
type Actor={page:Page;headers:Record<string,string>;subject:string;run?:{id:string;started:number;thread_id:string;status:string}}

test('bounded mixed lifecycle soak with two research tasks and one index lane',async({browser})=>{
  test.skip(!Number.isInteger(seconds)||seconds<30,'Explicit E2E_SOAK_SECONDS required; acceptance uses 1800 seconds.')
  expect(process.env.E2E_COMPOSE_PROJECT,'Use the isolated acceptance project').toBe('dr-lifecycle-acceptance')
  const base=new URL(process.env.E2E_BASE_URL || '')
  expect(['127.0.0.1','localhost']).toContain(base.hostname);expect(base.port).toBe('28080')
  test.setTimeout((seconds+360)*1000)
  const started=Date.now(),tag=randomUUID().slice(0,8),out=resolve('../.cache/lifecycle-soak',`${new Date().toISOString().replaceAll(':','-')}-${tag}`)
  mkdirSync(out,{recursive:true})
  const summary={seconds,started_at:new Date(started).toISOString(),finished_at:'',model_mode:'deterministic fixture; no quality or provider-SLA claim',research_policy:'internal RAG with one stable document per research workspace; separate workspace for index replacements',tasks:[] as {id:string;status:string;elapsed_ms:number;actor:string;sources:number}[],indexes:[] as {version:number;status:string;elapsed_ms:number;running_research_ids_at_submit:string[];same_research_running_at_completion:boolean}[],samples:[] as Record<string,unknown>[],authentication_renewals:[] as {actor:string;elapsed_ms:number}[],cleanup_cancellations:[] as {run_id:string;request_status:number;observed_status?:string}[],errors:[] as string[],settled:false}
  const contexts=await Promise.all([browser.newContext(),browser.newContext(),browser.newContext()])
  const actors:Actor[]=[]
  let docId='',docPending=false,docStarted=0,indexVersion=0,nextIndexAt=0,indexResearchIds:string[]=[]
  async function assertOk(response:Awaited<ReturnType<Page['request']['get']>>,label:string){expect(response.ok(),`${label}: HTTP ${response.status()}`).toBeTruthy();return response.json()}
  async function refreshAuthentication(actor:Actor){
    const token=actor.headers.Authorization.slice('Bearer '.length)
    const expires=Number(JSON.parse(Buffer.from(token.split('.')[1] || '', 'base64url').toString()).exp || 0)*1000
    if(expires>Date.now()+30_000)return
    // APIRequestContext bypasses the browser client's refresh logic. Trigger that actual
    // client before expiry, then copy its new access token for the next polling interval.
    const refreshed=actor.page.waitForResponse(response=>new URL(response.url()).pathname==='/api/documents'&&response.request().method()==='GET')
    await actor.page.getByRole('button',{name:/本地资料库/}).click()
    expect((await refreshed).ok(),'Authenticated UI renewal before access-token expiry').toBe(true)
    const refreshedToken=await actor.page.evaluate(()=>sessionStorage.getItem('dr-token'))
    if(refreshedToken!==token)summary.authentication_renewals.push({actor:actor.subject,elapsed_ms:Date.now()-started})
    actor.headers.Authorization=`Bearer ${refreshedToken}`
  }
  async function sampleRun(actor:Actor){
    if(!actor.run)return
    const run=await assertOk(await actor.page.request.get(`/api/research/runs/${actor.run.id}`,{headers:actor.headers}),'poll run')
    actor.run.status=run.status
    if(['completed','insufficient','failed','cancelled','interrupted'].includes(run.status)){
      summary.tasks.push({id:actor.run.id,status:run.status,elapsed_ms:Date.now()-actor.run.started,actor:actor.subject,sources:run.sources?.length || 0})
      expect(run.status,JSON.stringify({error:run.error,error_info:run.error_info,validation:run.validation})).toBe('completed')
      expect(run.sources?.some((source:{kind:string})=>source.kind==='local'),'Each accepted research must exercise local retrieval').toBe(true)
      actor.run=undefined
    }
  }
  async function launch(actor:Actor){
    const created=await actor.page.request.post('/api/research/runs',{headers:actor.headers,data:{topic:`slow-recovery mixed-soak ${tag} ${summary.tasks.length} 根据 soak-research-${tag}.md，如何组织研究流程与研究记忆？`,mode:'deep',data_policy:'internal',client_request_id:randomUUID()}})
    expect(created.status()).toBe(202)
    const run=await created.json();actor.run={id:run.id,thread_id:run.thread_id,started:Date.now(),status:run.status}
  }
  async function sampleIndex(){
    if(!docPending)return
    const documents=await assertOk(await actors[2]!.page.request.get('/api/documents',{headers:actors[2]!.headers}),'poll documents')
    const doc=documents.find((item:{id:string})=>item.id===docId)
    expect(doc,'Accepted document must remain visible').toBeTruthy()
    if(!['indexing','rebuilding','scanning'].includes(doc.status)){
      const stillRunning=actors.slice(0,2).filter(actor=>actor.run?.status==='running').map(actor=>actor.run!.id)
      summary.indexes.push({version:doc.index_version,status:doc.status,elapsed_ms:Date.now()-docStarted,running_research_ids_at_submit:[...indexResearchIds],same_research_running_at_completion:indexResearchIds.length===2&&indexResearchIds.every(id=>stillRunning.includes(id))})
      expect(doc.status,JSON.stringify({error:doc.error})).toBe('ready')
      expect(doc.error,'A failed replacement retaining the old version is not a completed index').toBeFalsy()
      expect(doc.index_version).toBeGreaterThanOrEqual(indexVersion)
      docPending=false;nextIndexAt=Date.now()+15_000
    }
  }
  try{
    const credentials=[
      [process.env.E2E_USERNAME,process.env.E2E_PASSWORD,process.env.E2E_AUTHOR_SUBJECT],
      [process.env.E2E_REVIEWER_USERNAME,process.env.E2E_REVIEWER_PASSWORD,process.env.E2E_REVIEWER_SUBJECT],
      [process.env.E2E_VIEWER_USERNAME,process.env.E2E_VIEWER_PASSWORD,process.env.E2E_VIEWER_SUBJECT],
    ]
    for(let i=0;i<3;i++){
      const account=credentials[i]!,page=await contexts[i]!.newPage()
      expect(account.every(Boolean),'Provision three OIDC users').toBeTruthy()
      await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click()
      await page.locator('#username').fill(account[0]!);await page.locator('#password').fill(account[1]!);await page.locator('#kc-login').click()
      await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:30_000})
      actors.push({page,subject:account[2]!,headers:{Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':account[2]!}})
    }
    for(const actor of actors.slice(0,2)){
      const response=await actor.page.request.post('/api/documents',{headers:actor.headers,multipart:{file:{name:`soak-research-${tag}.md`,mimeType:'text/markdown',buffer:Buffer.from('# 研究工作流与记忆\n\n研究助手先拆解问题，再收集资料，随后核查证据与引用。报告需要说明证据不足及统计口径冲突，不应编造数据。\n\n会话记忆保存研究上下文，用户偏好单独保存。历史摘要仅用于定位旧研究，新的事实判断需要重新查阅原始证据。\n')}}})
      expect(response.status()).toBe(202);const doc=await response.json()
      let indexed:any
      await expect.poll(async()=>{const documents=await assertOk(await actor.page.request.get('/api/documents',{headers:actor.headers}),'prepare research source');indexed=documents.find((item:{id:string})=>item.id===doc.id);return indexed?.status},{timeout:120_000,intervals:[1000,2000]}).toMatch(/^(ready|failed|quarantined|scan_failed)$/)
      expect(indexed.status,JSON.stringify({error:indexed.error})).toBe('ready')
    }
    const deadline=Date.now()+seconds*1000
    let nextSample=0
    while(Date.now()<deadline){
      if(existsSync(resolve(out,'operator-stop-request.json')))throw new Error('operator_stop_for_validator_fix')
      await Promise.all(actors.map(refreshAuthentication))
      await Promise.all(actors.slice(0,2).map(sampleRun))
      await Promise.all(actors.slice(0,2).filter(actor=>!actor.run).map(launch))
      if(docId)await sampleIndex()
      if(!docPending&&Date.now()>=nextIndexAt){
        const actor=actors[2]!,name=`soak-${tag}.md`;indexVersion++;docStarted=Date.now()
        indexResearchIds=actors.slice(0,2).filter(actor=>actor.run?.status==='running').map(actor=>actor.run!.id)
        const response=await actor.page.request.fetch(docId?`/api/documents/${docId}`:'/api/documents',{method:docId?'PUT':'POST',headers:actor.headers,multipart:{file:{name,mimeType:'text/markdown',buffer:Buffer.from(`# 混合运行验收\n\n资料更新序号 ${indexVersion}，仅用于隔离索引链路验证。\n`)}}})
        const doc=await assertOk(response,'submit document');docId=doc.id;docPending=true
      }
      if(Date.now()>=nextSample){
        const metrics=await Promise.all(actors.map(async actor=>({subject:actor.subject,metrics:await assertOk(await actor.page.request.get('/api/metrics',{headers:actor.headers}),'metrics'),dead_letters:await assertOk(await actor.page.request.get(`/api/workspaces/${actor.subject}/dead-letters`,{headers:actor.headers}),'dead letters')})))
        summary.samples.push({elapsed_ms:Date.now()-started,accepted_research:actors.slice(0,2).filter(actor=>actor.run).length,running_research:actors.slice(0,2).filter(actor=>actor.run?.status==='running').length,index_pending:docPending,metrics})
        writeFileSync(resolve(out,'progress.json'),JSON.stringify(summary,null,2))
        nextSample=Date.now()+60_000
      }
      await pause(2000)
    }
    const settleDeadline=Date.now()+180_000
    while((actors.some(actor=>actor.run)||docPending)&&Date.now()<settleDeadline){await Promise.all(actors.map(refreshAuthentication));await Promise.all(actors.slice(0,2).map(sampleRun));if(docId)await sampleIndex();await pause(2000)}
    summary.settled=!actors.some(actor=>actor.run)&&!docPending
    expect(summary.settled,'All accepted work must settle after intake stops').toBe(true)
    expect(summary.tasks.length).toBeGreaterThanOrEqual(2)
    expect(summary.indexes.length).toBeGreaterThanOrEqual(1)
    expect(summary.indexes.some(index=>index.same_research_running_at_completion),'An actual index completion must overlap the same two DB-running research tasks').toBe(true)
  }catch(error){summary.errors.push(error instanceof Error?error.message:String(error));throw error}
  finally{
    if(!summary.settled)for(const actor of actors.filter(actor=>actor.run)){
      try{
        await refreshAuthentication(actor)
        const response=await actor.page.request.post(`/api/research/runs/${actor.run!.id}/cancel`,{headers:actor.headers})
        const body=await response.json().catch(()=>({}))
        summary.cleanup_cancellations.push({run_id:actor.run!.id,request_status:response.status(),observed_status:body.status})
        if(!response.ok())summary.errors.push(`cleanup cancel HTTP ${response.status()} for ${actor.run!.id}`)
      }catch(error){summary.errors.push(`cleanup cancel: ${error instanceof Error?error.message:String(error)}`)}
    }
    summary.finished_at=new Date().toISOString();writeFileSync(resolve(out,'summary.json'),JSON.stringify(summary,null,2))
    console.log(`Mixed soak evidence: ${out}; completed research=${summary.tasks.length}, index=${summary.indexes.length}, settled=${summary.settled}`)
    // The provisioner removes the exact temporary workspaces after this test; accepted work is
    // allowed to settle first. A failure is retained in summary rather than erased by retries.
    await Promise.all(contexts.map(context=>context.close()))
  }
})
