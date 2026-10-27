import {test,expect,type Page} from '@playwright/test'
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
import {execFileSync} from 'node:child_process'
import {randomUUID,createHash} from 'node:crypto'

const directory=resolve('../.cache/optimization-20261026')
type Account={id:string;username:string;password:string}
const accounts:Record<string,Account>=process.env.E2E_OPTIMIZATION==='1'?JSON.parse(readFileSync(resolve(directory,'accounts.private.json'),'utf8')):{}
const config=process.env.E2E_OPTIMIZATION==='1'?JSON.parse(readFileSync(resolve(directory,'acceptance-config.json'),'utf8')):{}
const output=resolve(directory,'browser');mkdirSync(output,{recursive:true})
test.use({actionTimeout:15_000,navigationTimeout:30_000})
function fixtureSql(sql:string){
 expect(config.project).toBe('dr-opt-acceptance')
 return execFileSync('docker',['compose','--env-file',config.env_file,...config.compose_files.flatMap((file:string)=>['-f',file]),'-p',config.project,'exec','-T','postgres','psql','-U','deepresearch','-d','deepresearch','-Atc',sql],{encoding:'utf8',cwd:resolve('..')}).trim()
}
async function login(page:Page,role:string){
 await page.goto('/');await page.getByRole('button',{name:'登录并开始研究'}).click()
 await page.locator('#username').fill(accounts[role].username);await page.locator('#password').fill(accounts[role].password);await page.locator('#kc-login').click()
 await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:40_000})
 return {Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`,'X-Workspace-ID':accounts.author.id}
}
async function start(page:Page,topic:string,policy='internal',mode='quick'){
 await page.getByRole('button',{name:'新建研究',exact:true}).click();await page.getByLabel('资料范围',{exact:true}).selectOption(policy)
 await page.getByLabel('研究模式',{exact:true}).selectOption(mode);await page.getByLabel('研究主题',{exact:true}).fill(topic)
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/research/runs'&&r.request().method()==='POST')
 await page.getByRole('button',{name:'开始研究',exact:true}).click();const created=await response;expect(created.status()).toBe(202);return created.json()
}
async function terminal(page:Page,id:string,headers:Record<string,string>){
 let run:any
 await expect.poll(async()=>{const response=await page.request.get(`/api/research/runs/${id}`,{headers});expect(response.status()).toBe(200);run=await response.json();return run.status},{timeout:120_000,intervals:[500,1000,2000]}).toMatch(/^(completed|insufficient|failed|cancelled|interrupted)$/)
 return run
}
function receipt(name:string,value:unknown){writeFileSync(resolve(output,`${name}.json`),JSON.stringify({mode:'real browser/OIDC/Java/Python/SQL/storage; deterministic model/embedding/search fixture',...value as object},null,2))}

test('five roles: scope confirmation, collection grant/revoke, frozen report and download',async({browser})=>{
 test.skip(process.env.E2E_OPTIMIZATION!=='1','Explicit isolated candidate only');test.setTimeout(600_000)
 expect(config.project).toBe('dr-opt-acceptance');expect(new URL(config.web_url).port).toBe('28081')
 const contexts=await Promise.all(Object.keys(accounts).map(()=>browser.newContext())),pages:Record<string,Page>={}
 Object.keys(accounts).forEach((role,index)=>{pages[role]=null as unknown as Page})
 try{
  for(const [index,role] of Object.keys(accounts).entries())pages[role]=await contexts[index].newPage()
  const author=pages.author,ah=await login(author,'author')
  await author.getByRole('button',{name:'工作台设置',exact:true}).click()
  for(const [role,value] of [['reviewer','admin'],['researcher','researcher'],['viewer','viewer']]){
   await author.getByLabel('已注册用户的账号 ID').fill(accounts[role].id);await author.getByLabel('工作空间角色',{exact:true}).selectOption(value)
   const added=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/members')&&r.request().method()==='PUT');await author.getByRole('button',{name:'添加或更新成员'}).click();expect((await added).ok()).toBeTruthy()
  }
  const rh=await login(pages.reviewer,'reviewer'),vh=await login(pages.viewer,'viewer'),researcherHeaders=await login(pages.researcher,'researcher'),oh=await login(pages.outsider,'outsider')
  expect((await pages.outsider.request.get('/api/documents',{headers:oh})).status()).toBe(403)
  expect((await pages.viewer.request.post('/api/research/runs',{headers:vh,data:{topic:'read only',data_policy:'public',client_request_id:randomUUID()}})).status()).toBe(403)
  expect((await pages.researcher.request.post('/api/collections',{headers:researcherHeaders,data:{name:'forbidden',visibility:'restricted'}})).status()).toBe(403)
  await author.getByRole('button',{name:/^本地资料库/}).click()
  const upload=author.waitForResponse(r=>new URL(r.url()).pathname==='/api/documents'&&r.request().method()==='POST')
  await author.locator('input[type=file][multiple]').setInputFiles({name:'visitor-policy.md',mimeType:'text/markdown',buffer:Buffer.from('# 访客授权\n访客授权默认有效期7天。涉及受限集合的访问不得自动续期，必须由空间管理员重新授权。离开工作空间的账号立即失去读取权限。')})
  const uploaded=await upload;expect(uploaded.status()).toBe(202);const doc=(await uploaded.json()).id
  await expect.poll(async()=>{const rows=await(await author.request.get('/api/documents',{headers:ah})).json();return rows.find((row:any)=>row.id===doc)?.status},{timeout:120_000}).toBe('ready')
  await author.getByText('资料集合与访问权限',{exact:true}).click();await author.getByLabel('集合名称',{exact:true}).fill('受限访客制度')
  const createdCollection=author.waitForResponse(r=>new URL(r.url()).pathname==='/api/collections'&&r.request().method()==='POST');await author.getByRole('button',{name:'创建集合',exact:true}).click();const collection=(await(await createdCollection).json()).id
  await author.getByLabel('选择集合',{exact:true}).selectOption(collection);await author.getByLabel('分配资料',{exact:true}).selectOption(doc)
  const assigned=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/collection')&&r.request().method()==='PUT');await author.getByRole('button',{name:'将资料分配到所选集合'}).click();expect((await assigned).ok()).toBeTruthy()
  expect((await pages.viewer.request.get(`/api/documents/${doc}/chunks`,{headers:vh})).status()).toBe(422)
  await author.getByLabel('成员账号 ID',{exact:true}).fill(accounts.viewer.id);await author.getByLabel('成员集合权限').selectOption('read')
  const grant=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/grants'));await author.getByRole('button',{name:'保存成员权限'}).click();expect((await grant).ok()).toBeTruthy()
  expect((await pages.viewer.request.get(`/api/documents/${doc}/chunks`,{headers:vh})).ok()).toBeTruthy()
  const run=await start(author,'根据访客授权制度回答，默认有效期多久？受限集合能否自动续期？')
  const completed=await terminal(author,run.id,ah);expect(completed.status).toBe('completed');expect(completed.report).toContain('7');expect(completed.report).toMatch(/不得自动续期/)
  await expect(author.getByRole('button',{name:'提交人工审核',exact:true})).toBeEnabled({timeout:15_000})
  const submitted=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/publication')&&r.request().method()==='POST');await author.getByRole('button',{name:'提交人工审核'}).click();const publication=await(await submitted).json()
  expect((await author.request.post(`/api/reports/${publication.id}/approve`,{headers:ah,data:{reason:'self review must be denied'}})).status()).toBe(403)
  await pages.reviewer.getByRole('button',{name:'正式报告',exact:true}).click();await pages.reviewer.locator('.report-list-item').filter({hasText:completed.topic}).first().click()
  const reason=pages.reviewer.getByPlaceholder('说明可发布范围、尚未回答的问题或驳回原因');await expect(reason).toBeVisible();await reason.fill('仅限所引访客制度范围，未推断额外事实。')
  const [approved]=await Promise.all([pages.reviewer.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/approve')),pages.reviewer.getByRole('button',{name:'批准发布',exact:true}).click()]);expect(approved.ok()).toBeTruthy()
  await pages.viewer.getByRole('button',{name:'正式报告',exact:true}).click();await pages.viewer.locator('.report-list-item').filter({hasText:completed.topic}).first().click()
  const download=pages.viewer.waitForEvent('download');await pages.viewer.getByRole('button',{name:'下载正式版本',exact:true}).click();await(await download).saveAs(resolve(output,'frozen-report.md'))
  const before=await(await pages.viewer.request.get(`/api/reports/${publication.id}`,{headers:vh})).json();expect(readFileSync(resolve(output,'frozen-report.md'),'utf8')).toBe(before.report_markdown)
  await author.getByRole('button',{name:/^本地资料库/}).click();await author.getByText('资料集合与访问权限',{exact:true}).click();await author.getByLabel('选择集合',{exact:true}).selectOption(collection);await author.getByLabel('成员账号 ID',{exact:true}).fill(accounts.viewer.id);await author.getByLabel('成员集合权限').selectOption('none')
  const revoke=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/grants'));await author.getByRole('button',{name:'保存成员权限'}).click();expect((await revoke).ok()).toBeTruthy()
  expect((await pages.viewer.request.get(`/api/reports/${publication.id}`,{headers:vh})).status()).toBe(404)
  expect((await pages.viewer.request.get(`/api/reports/${publication.id}/download`,{headers:vh})).status()).toBe(404)
  const after=await(await author.request.get(`/api/reports/${publication.id}`,{headers:ah})).json();expect(after.content_sha256).toBe(before.content_sha256);expect(after.report_markdown).toBe(before.report_markdown)
  await author.getByRole('button',{name:'新建研究',exact:true}).click();await author.getByLabel('资料范围',{exact:true}).selectOption('public');await author.getByLabel('研究模式',{exact:true}).selectOption('deep');await author.getByLabel('研究主题',{exact:true}).fill('全面研究行业趋势')
  await author.getByRole('button',{name:'开始研究',exact:true}).click();await expect(author.getByRole('heading',{name:'先确认这次研究的范围'})).toBeVisible()
  await author.getByLabel('时间范围',{exact:true}).fill('2024—2026年');await author.getByLabel('地区范围',{exact:true}).fill('中国');await author.getByLabel('研究对象',{exact:true}).fill('企业AI应用');await author.getByLabel('交付标准',{exact:true}).fill('比较价值与限制，并披露证据缺口')
  await author.getByRole('button',{name:'保存范围',exact:true}).click();await expect(author.getByRole('status').filter({hasText:'范围已保存'})).toBeVisible()
  await author.reload();await author.getByRole('button',{name:'全面研究行业趋势',exact:true}).click();await expect(author.getByLabel('时间范围',{exact:true})).toHaveValue('2024—2026年')
  const confirmation=author.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/confirm'));await author.getByRole('button',{name:'确认并开始研究',exact:true}).click();const scoped=await(await confirmation).json();const scopedResult=await terminal(author,scoped.id,ah);expect(scopedResult.topic).toContain('用户确认口径')
  await author.screenshot({path:resolve(output,'confirmed-research.png'),fullPage:true})
  receipt('roles-and-scope',{passed:true,roles:Object.keys(accounts),document_id:doc,collection_id:collection,report_id:publication.id,run_id:run.id,scope_run_id:scoped.id,frozen_snapshot_sha256:before.content_sha256,downloaded_markdown_sha256:createHash('sha256').update(readFileSync(resolve(output,'frozen-report.md'))).digest('hex'),checks:['actual login','admin membership UI','viewer/cross-space denial','collection grant/revoke','draft to independent publication','historical snapshot retained','scope save/reload/confirm']})
 }finally{await Promise.all(contexts.map(context=>context.close()))}
})

test('Java recovery performs new retrieval; cancel, explicit public check and static diagnostics',async({browser})=>{
 test.skip(process.env.E2E_OPTIMIZATION!=='1','Explicit isolated candidate only');test.setTimeout(420_000)
 const context=await browser.newContext(),page=await context.newPage()
 try{
  const headers=await login(page,'author')
  // Prepare this case through the real upload path; another case may fail before seeding documents.
  await page.getByRole('button',{name:/^本地资料库/}).click()
  const upload=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/documents'&&r.request().method()==='POST')
  await page.locator('input[type=file][multiple]').setInputFiles({name:'recovery-visitor-policy.md',mimeType:'text/markdown',buffer:Buffer.from(`# 访客授权制度（恢复验收）\n访客授权默认有效期7天。涉及受限集合的访问不得自动续期，必须由空间管理员重新授权。离开工作空间的账号立即失去读取权限。\n验收资料编号：${randomUUID()}`)})
  const uploaded=await upload;expect(uploaded.status()).toBe(202);const doc=(await uploaded.json()).id
  await expect.poll(async()=>{const response=await page.request.get('/api/documents',{headers});expect(response.status()).toBe(200);return(await response.json()).find((row:any)=>row.id===doc)?.status},{timeout:120_000}).toBe('ready')
  fixtureSql("INSERT INTO metadata(key,value) VALUES('optimization-retrieval-offline','1') ON CONFLICT(key) DO UPDATE SET value='1'")
  const run=await start(page,'恢复取证：根据访客授权制度，默认有效期多久？')
  const failed=await terminal(page,run.id,headers);expect(failed.status).toBe('failed');expect(failed.validation.retrieval_failed).toBe(true)
  const attemptsBefore=Number(fixtureSql("SELECT value FROM metadata WHERE key='optimization-retrieval-attempts'"))
  fixtureSql("UPDATE metadata SET value='0' WHERE key='optimization-retrieval-offline'")
  const resumed=await page.request.post(`/api/research/runs/${run.id}/resume`,{headers});expect(resumed.status()).toBe(202)
  const recovered=await terminal(page,run.id,headers);expect(recovered.status).toBe('completed');expect(Number(fixtureSql("SELECT value FROM metadata WHERE key='optimization-retrieval-attempts'"))).toBeGreaterThan(attemptsBefore)
  expect(recovered.attempt_count).toBeGreaterThan(failed.attempt_count)
  const cancelRun=await start(page,'slow-recovery 取消流程：只核对访客授权制度')
  expect((await page.request.post(`/api/research/runs/${cancelRun.id}/cancel`,{headers})).status()).toBe(200)
  expect((await terminal(page,cancelRun.id,headers)).status).toBe('cancelled')
  const publicRun=await start(page,'比较线上课程与线下课程的差异和局限，并给公开依据。','public')
  const publicResult=await terminal(page,publicRun.id,headers);expect(publicResult.status).toBe('completed')
  const submitted=await page.request.post(`/api/research/runs/${publicRun.id}/publication`,{headers});expect(submitted.status()).toBe(201);const report=await submitted.json()
  const before=await(await page.request.get(`/api/reports/${report.id}`,{headers})).json()
  const check=await page.request.post(`/api/reports/${report.id}/source-rechecks`,{headers});expect(check.status()).toBe(200);expect((await check.json()).checks.length).toBeGreaterThan(0)
  const after=await(await page.request.get(`/api/reports/${report.id}`,{headers})).json();expect(after.content_sha256).toBe(before.content_sha256);expect(after.report_markdown).toBe(before.report_markdown)
  await page.getByRole('button',{name:'工作台设置',exact:true}).click();await page.getByRole('button',{name:'检查服务配置',exact:true}).click();await expect(page.locator('.connection-result')).toContainText('static')
  expect((await page.request.post('/api/config/probe?target=llm',{headers})).status()).toBe(403)
  receipt('recovery-and-sources',{passed:true,document_id:doc,failed_run_id:run.id,attempts_before:attemptsBefore,attempts_after:Number(fixtureSql("SELECT value FROM metadata WHERE key='optimization-retrieval-attempts'")),cancelled_run_id:cancelRun.id,public_report_id:report.id,snapshot_unchanged:true,diagnostic_paid_probe_denied:true})
 }finally{fixtureSql("UPDATE metadata SET value='0' WHERE key='optimization-retrieval-offline'");await context.close()}
})
