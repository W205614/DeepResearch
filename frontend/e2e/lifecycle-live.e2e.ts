import { expect, test, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const accounts = {
  author:[process.env.E2E_USERNAME,process.env.E2E_PASSWORD,process.env.E2E_AUTHOR_SUBJECT],
  reviewer:[process.env.E2E_REVIEWER_USERNAME,process.env.E2E_REVIEWER_PASSWORD,process.env.E2E_REVIEWER_SUBJECT],
  viewer:[process.env.E2E_VIEWER_USERNAME,process.env.E2E_VIEWER_PASSWORD,process.env.E2E_VIEWER_SUBJECT],
}
async function login(page:Page, account:(string|undefined)[]) {
  await page.goto('/')
  await page.getByRole('button',{name:'登录并开始研究'}).click()
  await page.locator('#username').fill(account[0]!)
  await page.locator('#password').fill(account[1]!)
  await page.locator('#kc-login').click()
  await expect(page.getByRole('button',{name:/退出/})).toBeVisible({timeout:30_000})
  return {Authorization:`Bearer ${await page.evaluate(()=>sessionStorage.getItem('dr-token'))}`, 'X-Workspace-ID':accounts.author[2]!}
}
async function waitRun(page:Page, id:string, headers:Record<string,string>) {
  let result:any
  await expect.poll(async()=>{
    const response=await page.request.get(`/api/research/runs/${id}`,{headers})
    expect(response.ok()).toBeTruthy()
    result=await response.json()
    return result.status
  },{timeout:300_000,intervals:[1000,2000,4000]}).toMatch(/^(completed|insufficient|failed|cancelled|interrupted)$/)
  expect(result.status,JSON.stringify({error:result.error,validation:result.validation})).toBe('completed')
  return result
}

test('real services: document update, impact, revision, independent review, viewer and revoked boundaries', async ({browser})=>{
  test.skip(process.env.E2E_LIFECYCLE!=='1','Run explicitly against the isolated lifecycle stack; no request interception or database fixtures.')
  expect(['dr-lifecycle-acceptance','dr-opt-acceptance'],'Bind lifecycle acceptance to an explicit isolated project').toContain(process.env.E2E_COMPOSE_PROJECT)
  const base=new URL(process.env.E2E_BASE_URL || '')
  expect(['127.0.0.1','localhost']).toContain(base.hostname);expect(base.port).toBe(process.env.E2E_COMPOSE_PROJECT==='dr-opt-acceptance'?'28081':'28080')
  for(const [role,values] of Object.entries(accounts))expect(values.every(Boolean),`${role} credentials must be provisioned`).toBeTruthy()
  test.setTimeout(720_000)
  const contexts=await Promise.all([browser.newContext(),browser.newContext(),browser.newContext()])
  const [author,reviewer,viewer]=await Promise.all(contexts.map(context=>context.newPage()))
  const tag=randomUUID().slice(0,8), name=`lifecycle-policy-${tag}.md`
  let docId='', oldReportId='', newReportId='', publishedSnapshot:unknown=null, reviewerHeaders:Record<string,string>|null=null, authorHeaders:Record<string,string>|null=null
  const threadIds=new Set<string>()
  try {
    authorHeaders=await login(author,accounts.author)
    for(const [subject,role] of [[accounts.reviewer[2],'admin'],[accounts.viewer[2],'viewer']]) {
      const added=await author.request.put(`/api/workspaces/${accounts.author[2]}/members`,{headers:authorHeaders,data:{subject,role}})
      expect(added.ok()).toBeTruthy()
    }
    reviewerHeaders=await login(reviewer,accounts.reviewer)
    const viewerHeaders=await login(viewer,accounts.viewer)
    await reviewer.getByLabel('切换工作空间').selectOption(accounts.author[2]!)
    await viewer.getByLabel('切换工作空间').selectOption(accounts.author[2]!)

    await author.getByRole('button',{name:/本地资料库/}).click()
    await author.locator('input[type=file][multiple]').setInputFiles({name,mimeType:'text/markdown',buffer:Buffer.from('# 报销制度\n\n正式员工报销上限为 500 元。实习生不适用本制度。\n')})
    let originalVersion=0
    await expect.poll(async()=>{
      const response=await author.request.get('/api/documents',{headers:authorHeaders!})
      const doc=(await response.json()).find((item:any)=>item.name===name)
      if(doc){docId=doc.id;originalVersion=doc.index_version}
      return doc?.status
    },{timeout:120_000,intervals:[1000,2000]}).toBe('ready')
    await author.getByRole('button',{name:'新建研究',exact:true}).click()
    await author.getByLabel('资料范围',{exact:true}).selectOption('internal')
    await author.getByLabel('研究主题',{exact:true}).fill(`根据 ${name}，正式员工报销上限是多少？实习生是否适用？请说明依据和适用条件。`)
    const createdPromise=author.waitForResponse(response=>new URL(response.url()).pathname==='/api/research/runs'&&response.request().method()==='POST')
    await author.getByRole('button',{name:'开始研究',exact:true}).click()
    const created=await createdPromise
    expect(created.status()).toBe(202)
    const initialRun=await created.json();threadIds.add(initialRun.thread_id)
    const first=await waitRun(author,initialRun.id,authorHeaders)
    expect(first.sources.some((source:any)=>source.document_id===docId)).toBeTruthy()
    expect(first.validation.quality).toMatch(/^(complete|partial)$/)
    await expect(author.getByRole('button',{name:'提交人工审核'})).toBeEnabled({timeout:15_000})
    const submitPromise=author.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/publication')&&response.request().method()==='POST')
    await author.getByRole('button',{name:'提交人工审核'}).click()
    const submitted=await submitPromise;expect(submitted.status()).toBe(201)
    oldReportId=(await submitted.json()).id
    // Author is an administrator, but may not approve their own snapshot.
    expect((await author.request.post(`/api/reports/${oldReportId}/approve`,{headers:authorHeaders,data:{reason:'自审禁止检查'}})).status()).toBe(403)
    await expect(author.getByText('报告作者不能审核自己的报告，请由另一位管理员处理。')).toBeVisible()
    await reviewer.getByRole('button',{name:'正式报告',exact:true}).click()
    await reviewer.locator('.report-list-item').filter({hasText:name}).click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已核对资料、金额及适用条件。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.locator('.report-toolbar .pill')).toContainText('正式版本')
    expect((await viewer.request.get(`/api/reports/${oldReportId}`,{headers:viewerHeaders})).status()).toBe(200)
    expect((await viewer.request.post(`/api/reports/${oldReportId}/revisions`,{headers:viewerHeaders,data:{client_request_id:randomUUID(),expected_content_sha256:'a'.repeat(64),expected_corpus_revision:0}})).status()).toBe(403)
    // Invited accounts may have no personal workspace; workspace admission then returns 403
    // before report lookup. Both paths must deny access without exposing the shared report.
    expect([403,404]).toContain((await viewer.request.get(`/api/reports/${oldReportId}`,{headers:{...viewerHeaders,'X-Workspace-ID':accounts.viewer[2]!}})).status())

    await author.getByRole('button',{name:/本地资料库/}).click()
    const documentCard=author.locator('.list-card').filter({hasText:name})
    await documentCard.getByRole('button',{name:'替换',exact:true}).click({noWaitAfter:true})
    await author.locator('input[type=file]:not([multiple])').setInputFiles({name,mimeType:'text/markdown',buffer:Buffer.from('# 报销制度\n\n正式员工报销上限为 800 元。实习生仍不适用本制度。\n')})
    await expect.poll(async()=>{
      const docs=await (await author.request.get('/api/documents',{headers:authorHeaders!})).json()
      const doc=docs.find((item:any)=>item.id===docId)
      return doc?.status==='ready'&&doc.index_version>originalVersion
    },{timeout:120_000,intervals:[1000,2000]}).toBe(true)
    await documentCard.getByRole('button',{name:'版本历史',exact:true}).click()
    await author.getByRole('button',{name:'比较资料版本'}).click()
    await expect(author.getByRole('region',{name:'资料版本历史'})).toContainText('800 元')
    await author.getByRole('button',{name:'正式报告',exact:true}).click()
    // Selected report can survive navigation; refresh its evidence status before revising.
    if(await author.getByRole('button',{name:'返回报告列表',exact:true}).count())await author.getByRole('button',{name:'返回报告列表',exact:true}).click()
    await author.locator('.report-list-item').filter({hasText:name}).click()
    await expect(author.getByRole('region',{name:'报告版本与资料影响'})).toContainText(/待范围检查|需重新核验|影响范围无法判定/)
    const revisionResponse=author.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/revisions')&&response.request().method()==='POST')
    await author.getByRole('button',{name:'依据当前资料发起修订'}).click()
    const revision=await revisionResponse;expect(revision.status()).toBe(202)
    const revisionJob=await revision.json()
    await author.getByRole('button',{name:'查看修订进度',exact:true}).click()
    const revised=await waitRun(author,revisionJob.run_id,authorHeaders);threadIds.add(revised.thread_id)
    await expect(author.getByRole('button',{name:'提交人工审核'})).toBeEnabled({timeout:15_000})
    const revisedSubmit=author.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/publication')&&response.request().method()==='POST')
    await author.getByRole('button',{name:'提交人工审核'}).click()
    const newReport=await revisedSubmit;expect(newReport.status()).toBe(201)
    newReportId=(await newReport.json()).id
    expect((await viewer.request.get(`/api/reports/${newReportId}`,{headers:viewerHeaders})).status()).toBe(404)
    await reviewer.getByRole('button',{name:'正式报告',exact:true}).click()
    await reviewer.getByRole('button',{name:'返回报告列表',exact:true}).click()
    const pending=reviewer.locator('.report-list-item').filter({hasText:name}).filter({hasText:'待审核'})
    await pending.click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已核对新资料版本与修订结论。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.getByRole('button',{name:/第 2 版.*已发布.*当前正式版/})).toBeVisible()
    await reviewer.getByRole('button',{name:'比较报告差异'}).click()
    await expect(reviewer.getByRole('region',{name:'报告结论差异'})).toBeVisible()
    const published=await (await viewer.request.get(`/api/reports/${newReportId}`,{headers:viewerHeaders})).json()
    publishedSnapshot=published
    expect(published.parent_report_id).toBe(oldReportId)
    expect(published.revision_no).toBe(2)
    expect(published.sources.filter((source:any)=>source.document_id===docId).every((source:any)=>source.index_version>originalVersion)).toBe(true)
    await author.request.delete(`/api/workspaces/${accounts.author[2]}/members/${accounts.viewer[2]}`,{headers:authorHeaders})
    expect([403,404]).toContain((await viewer.request.get(`/api/reports/${newReportId}`,{headers:viewerHeaders})).status())
    await viewer.getByRole('button',{name:'正式报告',exact:true}).click()
    await expect(viewer.locator('.error-banner')).toBeVisible()
  } finally {
    if(process.env.E2E_PRESERVE_LIFECYCLE==='1'){
      const directory=resolve(process.env.E2E_PRESERVE_DIRECTORY||'../.cache/lifecycle-acceptance');mkdirSync(directory,{recursive:true})
      writeFileSync(resolve(directory,'preserved-role-result.json'),JSON.stringify({docId,oldReportId,newReportId,threadIds:[...threadIds],author_subject:accounts.author[2],reviewer_subject:accounts.reviewer[2],published:publishedSnapshot},null,2))
    }else{
    if(reviewerHeaders)for(const id of [newReportId,oldReportId].filter(Boolean)) {
      await reviewer.request.post(`/api/reports/${id}/withdraw`,{headers:reviewerHeaders,data:{reason:'隔离验收数据清理'}}).catch(()=>{})
      await reviewer.request.delete(`/api/reports/${id}`,{headers:reviewerHeaders}).catch(()=>{})
    }
    if(authorHeaders){
      if(docId)await author.request.delete(`/api/documents/${docId}`,{headers:authorHeaders}).catch(()=>{})
      for(const id of threadIds)await author.request.delete(`/api/threads/${id}`,{headers:authorHeaders}).catch(()=>{})
    }
    }
    await Promise.all(contexts.map(context=>context.close()))
  }
})
