import {test,expect,type Page} from '@playwright/test'
import {readFileSync,writeFileSync,mkdirSync,existsSync} from 'node:fs'
import {resolve} from 'node:path'
import {execFileSync} from 'node:child_process'
import {createHash} from 'node:crypto'

const root=resolve('../.cache/optimization-20261026')
const enabled=process.env.E2E_OPTIMIZATION_LIVE==='1'
const accounts=enabled?JSON.parse(readFileSync(resolve(root,'accounts.private.json'),'utf8')):{}
const config=enabled?JSON.parse(readFileSync(resolve(root,'acceptance-config.json'),'utf8')):{}
const frozen=enabled?JSON.parse(readFileSync(resolve('../docs/evidence/optimization-20261026/frozen-live-cases.json'),'utf8')):{}
const sha=(body:Buffer|string)=>createHash('sha256').update(body).digest('hex')
test.describe.configure({retries:0})

async function login(page:Page,role:string){await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click();await page.locator('#username').fill(accounts[role].username);await page.locator('#password').fill(accounts[role].password);await page.locator('#kc-login').click();await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:40_000});return {Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':accounts[role].id}}

test('two frozen isolated real-provider cases preserve every attempt and separate workspaces',async({browser})=>{
 test.skip(!enabled,'Explicit frozen real-provider acceptance only');test.setTimeout(1_500_000)
 expect(config.project).toBe('dr-opt-acceptance')
 for(const service of ['agent','worker','document-worker']){
  const raw=JSON.parse(execFileSync('docker',['inspect',`dr-opt-acceptance-${service}-1`],{encoding:'utf8'}))[0]
  expect(raw.Config.Cmd).toContain('scripts/bounded_server.py')
  expect(raw.Config.Env.some((value:string)=>value.startsWith('DATABASE_URL=')&&value.endsWith('/dr_opt_live'))).toBe(true)
 }
 const contexts=await Promise.all([browser.newContext(),browser.newContext(),browser.newContext()])
 const pages=await Promise.all(contexts.map(context=>context.newPage()))
 const out=resolve(root,'live');mkdirSync(out,{recursive:true})
 if(existsSync(resolve(out,'attempts.json')))throw new Error('Frozen live attempts already exist: inspect and resume their original IDs without creating new samples')
 writeFileSync(resolve(out,'frozen-cases.json'),JSON.stringify(frozen,null,2),{flag:'wx'})
 const results:any[]=[]
 try{
  const reviewer=pages[2],rh=await login(reviewer,'reviewer')
  for(const [index,role] of ['author','researcher'].entries()){
   const specimen=frozen.cases[index],page=pages[index],headers=await login(page,role)
   const record:any={id:specimen.id,split:specimen.split,question_sha256:sha(specimen.question),document_sha256:sha(specimen.document),workspace:accounts[role].id,attempt:1,status:'started'}
   results.push(record);writeFileSync(resolve(out,'attempts.json'),JSON.stringify(results,null,2))
   expect((await page.request.put(`/api/workspaces/${accounts[role].id}/members`,{headers,data:{subject:accounts.reviewer.id,role:'admin'}})).ok()).toBeTruthy()
   const upload=await page.request.post('/api/documents',{headers,multipart:{file:{name:`${specimen.id}.md`,mimeType:'text/markdown',buffer:Buffer.from(specimen.document)}}})
   record.upload_status=upload.status();expect(upload.status()).toBe(202);record.document_id=(await upload.json()).id
   await expect.poll(async()=>{const response=await page.request.get('/api/documents',{headers});return response.ok()?(await response.json()).find((row:any)=>row.id===record.document_id)?.status:'unavailable'},{timeout:180_000,intervals:[1000,2000]}).toBe('ready')
   await page.getByRole('button',{name:'新建研究',exact:true}).click();await page.getByLabel('资料范围',{exact:true}).selectOption('internal');await page.getByLabel('研究模式',{exact:true}).selectOption('quick');await page.getByLabel('研究主题',{exact:true}).fill(specimen.question)
   const [created]=await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname==='/api/research/runs'&&response.request().method()==='POST'),page.getByRole('button',{name:'开始研究',exact:true}).click()]);record.create_status=created.status();expect(created.status()).toBe(202);record.run_id=(await created.json()).id
   let run:any
   await expect.poll(async()=>{const response=await page.request.get(`/api/research/runs/${record.run_id}`,{headers});expect(response.ok()).toBeTruthy();run=await response.json();record.status=run.status;writeFileSync(resolve(out,`${specimen.id}-run.json`),JSON.stringify(run,null,2));writeFileSync(resolve(out,'attempts.json'),JSON.stringify(results,null,2));return run.status},{timeout:600_000,intervals:[1000,2000,3000]}).toMatch(/^(completed|insufficient|failed|cancelled|interrupted)$/)
   expect(run.status).toBe('completed');expect(run.sources.some((source:any)=>source.document_id===record.document_id)).toBe(true);expect(run.sources.every((source:any)=>source.kind==='local')).toBe(true)
   record.quality=run.validation.quality;record.report_sha256=sha(run.report)
   writeFileSync(resolve(out,`${specimen.id}-report.md`),run.report)
   await expect(page.getByRole('button',{name:'提交人工审核',exact:true})).toBeEnabled({timeout:15_000})
   const [submitted]=await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/publication')&&response.request().method()==='POST'),page.getByRole('button',{name:'提交人工审核',exact:true}).click()]);expect(submitted.status()).toBe(201);const draft=await submitted.json();record.report_id=draft.id
   const reviewed=await reviewer.request.post(`/api/reports/${draft.id}/approve`,{headers:{...rh,'X-Workspace-ID':accounts[role].id},data:{reason:'受控验收角色批准；仅限所提供合成规则支持的范围，不等于人工盲评。'}});expect(reviewed.ok()).toBeTruthy()
   const published=await(await page.request.get(`/api/reports/${draft.id}`,{headers})).json();record.snapshot_sha256=published.content_sha256;expect(published.status).toBe('published');expect(published.reviewed_by).not.toBe(published.author_subject)
   record.status='completed_and_published';writeFileSync(resolve(out,'attempts.json'),JSON.stringify(results,null,2));await page.screenshot({path:resolve(out,`${specimen.id}.png`),fullPage:true})
  }
 }finally{await Promise.all(contexts.map(context=>context.close()))}
})
