import {test,expect,type Page} from '@playwright/test'
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
import {execFileSync} from 'node:child_process'
import {randomUUID} from 'node:crypto'
test.use({actionTimeout:15_000,navigationTimeout:30_000})
const root=resolve('../.cache/optimization-20261026')
const enabled=process.env.E2E_OPTIMIZATION==='1'
const accounts=enabled?JSON.parse(readFileSync(resolve(root,'accounts.private.json'),'utf8')):{}
const config=enabled?JSON.parse(readFileSync(resolve(root,'acceptance-config.json'),'utf8')):{}
async function login(page:Page,role:string){await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click();await page.locator('#username').fill(accounts[role].username);await page.locator('#password').fill(accounts[role].password);await page.locator('#kc-login').click();await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:40_000});return {Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':accounts.author.id}}
function sourceVersion(value:'1'|'2'){expect(config.project).toBe('dr-opt-acceptance');execFileSync('docker',['compose','--env-file',config.env_file,...config.compose_files.flatMap((f:string)=>['-f',f]),'-p',config.project,'exec','-T','postgres','psql','-U','deepresearch','-d','deepresearch','-Atc',`INSERT INTO metadata(key,value) VALUES('optimization-public-version','${value}') ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value`],{cwd:resolve('..'),stdio:'pipe'})}
async function waitRun(page:Page,id:string,headers:Record<string,string>){let run:any;await expect.poll(async()=>{const r=await page.request.get(`/api/research/runs/${id}`,{headers});expect(r.ok()).toBeTruthy();run=await r.json();return run.status},{timeout:120_000,intervals:[500,1000,2000]}).toMatch(/^(completed|insufficient|failed|cancelled|interrupted)$/);expect(run.status).toBe('completed');return run}

test('public source recheck retains the published version and creates an independently reviewed revision',async({browser})=>{
 test.skip(!enabled,'Explicit isolated candidate only');test.setTimeout(420_000)
 const contexts=await Promise.all([browser.newContext(),browser.newContext()]),author=await contexts[0].newPage(),reviewer=await contexts[1].newPage()
 try{
  sourceVersion('1');const ah=await login(author,'author')
  expect((await author.request.put(`/api/workspaces/${accounts.author.id}/members`,{headers:ah,data:{subject:accounts.reviewer.id,role:'admin'}})).ok()).toBeTruthy()
  const rh=await login(reviewer,'reviewer')
  const created=await author.request.post('/api/research/runs',{headers:ah,data:{topic:'公开来源时效验收：比较线上课程和线下课程的适用条件与限制。',mode:'quick',data_policy:'public',client_request_id:randomUUID()}});expect(created.status()).toBe(202)
  const run=await waitRun(author,(await created.json()).id,ah)
  const submit=await author.request.post(`/api/research/runs/${run.id}/publication`,{headers:ah});expect(submit.status()).toBe(201);const original=await submit.json()
  expect((await reviewer.request.post(`/api/reports/${original.id}/approve`,{headers:rh,data:{reason:'仅在受控公开来源所述范围内批准。'}})).ok()).toBeTruthy()
  const before=await(await author.request.get(`/api/reports/${original.id}`,{headers:ah})).json()
  await author.getByRole('button',{name:'正式报告',exact:true}).click();await author.locator('.report-list-item').filter({hasText:run.topic}).first().click()
  sourceVersion('2');const [checked]=await Promise.all([author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/source-rechecks')&&r.request().method()==='POST'),author.getByRole('button',{name:'重新获取公开来源',exact:true}).click()]);expect(checked.status()).toBe(200);expect((await checked.json()).checks.some((row:any)=>row.status==='changed')).toBe(true)
  await expect(author.getByText('正文已变化，建议发起修订',{exact:false}).first()).toBeVisible()
  await expect(author.getByRole('button',{name:'依据当前资料发起修订',exact:true})).toBeEnabled()
  const [revision]=await Promise.all([author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/revisions')&&r.request().method()==='POST'),author.getByRole('button',{name:'依据当前资料发起修订',exact:true}).click()]);expect(revision.status()).toBe(202);const job=await revision.json()
  await author.getByRole('button',{name:'查看修订进度',exact:true}).click();const revised=await waitRun(author,job.run_id,ah);expect(revised.report).toContain('新增了适用范围和限制条件')
  await expect(author.getByRole('button',{name:'提交人工审核',exact:true})).toBeEnabled()
  const [resubmitted]=await Promise.all([author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/publication')&&r.request().method()==='POST'),author.getByRole('button',{name:'提交人工审核',exact:true}).click()]);expect(resubmitted.status()).toBe(201);const next=await resubmitted.json()
  await reviewer.getByRole('button',{name:'正式报告',exact:true}).click();await reviewer.locator('.report-list-item').filter({hasText:run.topic}).filter({hasText:'待审核'}).first().click()
  await reviewer.getByPlaceholder('说明可发布范围、尚未回答的问题或驳回原因').fill('已核对来源新版本；仅发布所引来源明确支持的结论。')
  const [approved]=await Promise.all([reviewer.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/approve')&&r.request().method()==='POST'),reviewer.getByRole('button',{name:'批准发布',exact:true}).click()]);expect(approved.ok()).toBeTruthy()
  await reviewer.getByRole('button',{name:'比较报告差异',exact:true}).click();await expect(reviewer.getByRole('region',{name:'报告结论差异'})).toBeVisible()
  const after=await(await author.request.get(`/api/reports/${original.id}`,{headers:ah})).json(),published=await(await author.request.get(`/api/reports/${next.id}`,{headers:ah})).json()
  expect(after.content_sha256).toBe(before.content_sha256);expect(after.report_markdown).toBe(before.report_markdown);expect(published.parent_report_id).toBe(original.id);expect(published.revision_no).toBe(2);expect(published.is_current).toBe(true)
  const rejectedRun=await author.request.post('/api/research/runs',{headers:ah,data:{topic:'根据当前公开来源说明适用范围。',mode:'quick',data_policy:'public',client_request_id:randomUUID()}});const rejectionDraft=await waitRun(author,(await rejectedRun.json()).id,ah);const rejection=await(await author.request.post(`/api/research/runs/${rejectionDraft.id}/publication`,{headers:ah})).json()
  expect((await reviewer.request.post(`/api/reports/${rejection.id}/reject`,{headers:rh,data:{reason:'受控验收：范围不足，退回补充。'}})).ok()).toBeTruthy()
  expect((await reviewer.request.post(`/api/reports/${next.id}/withdraw`,{headers:rh,data:{reason:'受控验收：保留历史，撤回当前发布。'}})).ok()).toBeTruthy()
  expect((await(await author.request.get(`/api/report-series/${published.series_id}`,{headers:ah})).json()).current_report_id).toBeNull()
  const out=resolve(root,'browser');mkdirSync(out,{recursive:true});await reviewer.screenshot({path:resolve(out,'public-source-revision.png'),fullPage:true});writeFileSync(resolve(out,'public-source-lifecycle.json'),JSON.stringify({passed:true,mode:'real browser/OIDC/Java/Python/SQL; controlled source text and deterministic model fixtures',original_report_id:original.id,revision_report_id:next.id,old_snapshot_sha256:before.content_sha256,old_snapshot_unchanged:true,new_version_independently_reviewed:true,reject_and_withdraw_preserve_history:true,supplier_http_requests:0},null,2))
 }finally{sourceVersion('1');await Promise.all(contexts.map(context=>context.close()))}
})
