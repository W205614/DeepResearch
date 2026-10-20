import { expect, test, type Page } from '@playwright/test'

// Contract/UI simulations only. Real authentication and authorization are checked in lifecycle-live.
async function simulatedAccount(page: Page, role = 'researcher', subject = 'author', pending = false, slowDiagnostics = false) {
  const state = { revision: 2, posts: [] as Record<string, unknown>[], rejectFirst: false }
  const token = `${Buffer.from('{}').toString('base64url')}.${Buffer.from(JSON.stringify({sub:subject, exp:Math.floor(Date.now()/1000)+3600})).toString('base64url')}.signature`
  await page.addInitScript(value => sessionStorage.setItem('dr-token',value), token)
  const report = (id = 'r1') => ({id, source_run_id:'run1', topic:id === 'r1' ? '报销研究' : '旧版报销研究', author_subject:'author', report_markdown:'# 报销研究\n上限 800 元。', sources:[], validation:{verification_contract:'verification-v3-claim-span',quality:'complete'}, content_sha256:'a'.repeat(64), status:pending?'pending':'published', submitted_at:'2026-10-20T00:00:00Z', reviewed_at:'2026-10-20T00:00:00Z', series_id:'s1', revision_no:id === 'r1'?2:1, corpus_revision:1, evidence_status:'check_pending'})
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname
    const json = (body:unknown,status=200,headers:Record<string,string>={}) => route.fulfill({status, contentType:'application/json',body:JSON.stringify(body),headers})
    if(path === '/api/auth/config')return json({mode:'oidc',issuer:'http://localhost:8480/realms/deepresearch'})
    if(path === '/api/workspaces')return json([{id:'w1',name:'研究',role},{id:'w2',name:'另一空间',role:'viewer'}])
    if(path === '/api/threads')return json([])
    if(path === '/api/status'){
      if(slowDiagnostics)await new Promise(resolve=>setTimeout(resolve,7000))
      return json({missing:[]})
    }
    if(path === '/api/capabilities')return json({reasons:[],submit_research:role!=='viewer'})
    if(path === '/api/documents')return json([{id:'d1',name:'报销制度.md',status:'ready',index_version:2,created_at:'2026-10-20T00:00:00Z'}])
    if(path === '/api/documents/d1/versions')return json([1,2].map(version=>({document_id:'d1',version,name:'报销制度.md',hash:`hash${version}`,status:'ready',created_at:'2026-10-20T00:00:00Z',current:version===2,chunk_count:1})))
    if(path === '/api/documents/d1/diff')return json({document_id:'d1',from_version:1,to_version:2,semantic_judgment:false,truncated:false,changes:[{kind:'modified',old:{locator:'条款1',text:'上限 500 元'},new:{locator:'条款1',text:'上限 800 元，但实习生不适用'}}]})
    if(path === '/api/reports')return json(pending?[]:[report()])
    if(path === '/api/reports/pending')return json(pending?[report()]:[])
    if(path === '/api/reports/archived')return json([])
    if(path === '/api/report-series/s1')return json({id:'s1',current_report_id:'r1',reports:[report(),report('r0')]})
    if(path === '/api/reports/r1/impacts' || path === '/api/reports/r0/impacts')return json({report_id:'r1',corpus_revision:1,current_corpus_revision:state.revision,evidence_status:'check_pending',impacts:[{target_revision:2,status:'needs_review',reasons:['报销金额及适用对象变化'],affected_claims:['claim-1'],diff:{document_id:'d1'}}],revision_jobs:[]})
    if(path === '/api/reports/r1/diff')return json({before_report:'上限 500 元',after_report:'上限 800 元',changes:[{kind:'changed',old:{id:'c1',text:'上限 500 元'},new:{id:'c1',text:'上限 800 元'}}],legacy:false})
    if(path === '/api/reports/r1/revisions') {
      state.posts.push(req.postDataJSON())
      if(state.rejectFirst && state.posts.length === 1)return json({detail:'报告或资料版本已变化'},409)
      return json({revision_job_id:'j1',run_id:'newrun',status:'queued'},202)
    }
    if(path === '/api/reports/r1' || path === '/api/reports/r0')return json(report(path.endsWith('r0')?'r0':'r1'))
    return json({detail:`Unhandled simulation ${path}`},404)
  })
  await page.goto('/')
  await expect(page.getByRole('button',{name:/退出/})).toBeVisible()
  return state
}

test('author compares document and claim versions, preserves retry identity and submits one revision', async ({page}) => {
  const state = await simulatedAccount(page)
  state.rejectFirst = true
  await page.getByRole('button',{name:/本地资料库/}).click()
  await page.getByRole('button',{name:'版本历史',exact:true}).click()
  await expect(page.getByRole('heading',{name:'报销制度.md · 版本历史'})).toBeVisible()
  await page.getByRole('button',{name:'比较资料版本'}).click()
  await expect(page.getByText('上限 800 元，但实习生不适用',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await page.locator('.report-list-item').filter({hasText:'报销研究'}).click()
  await expect(page.getByText('报销金额及适用对象变化')).toBeVisible()
  await page.getByRole('button',{name:'比较报告差异'}).click()
  await expect(page.getByText('修改结论',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'依据当前资料发起修订'}).click()
  await expect(page.getByRole('alert')).toContainText('资料版本已变化')
  await page.getByRole('button',{name:'依据当前资料发起修订'}).click()
  await expect(page.getByRole('button',{name:'修订已提交'})).toBeDisabled()
  expect(state.posts).toHaveLength(2)
  expect(state.posts[0]).toEqual(state.posts[1])
  expect(state.posts[0]).toMatchObject({expected_corpus_revision:2,expected_content_sha256:'a'.repeat(64)})
})

test('viewer cannot mutate documents or revise reports, including when viewer is original author', async ({page}) => {
  const state = await simulatedAccount(page,'viewer','author')
  await page.getByRole('button',{name:/本地资料库/}).click()
  await expect(page.getByRole('button',{name:'选择本地资料'})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'替换',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:/删除 报销制度/})).toHaveCount(0)
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await page.locator('.report-list-item').click()
  await expect(page.getByText('只读成员可以查看正式版本，不能发起修订。')).toBeVisible()
  await expect(page.getByRole('button',{name:'依据当前资料发起修订'})).toHaveCount(0)
  expect(state.posts).toHaveLength(0)
})

test('administrator cannot approve stale evidence or self-review', async ({page}) => {
  await simulatedAccount(page,'admin','reviewer',true)
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await page.locator('.report-list-item').click()
  await expect(page.getByRole('button',{name:'批准发布'})).toBeDisabled()
  await expect(page.getByText(/当前版本不能直接批准/)).toBeVisible()
})

test('historical report cannot start a revision and workspace switch clears its contents', async ({page}) => {
  await simulatedAccount(page)
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await page.locator('.report-list-item').click()
  await page.getByRole('button',{name:/第 1 版 · 旧版报销研究/}).click()
  await expect(page.getByText('这是历史版本，请从系列当前正式版发起修订。')).toBeVisible()
  await expect(page.getByRole('button',{name:'依据当前资料发起修订'})).toHaveCount(0)
  await page.getByLabel('切换工作空间').selectOption('w2')
  await expect(page.getByRole('region',{name:'报告版本与资料影响'})).toHaveCount(0)
})

test('report and thread pagination remains accessible without loading the entire archive', async ({page}) => {
  await simulatedAccount(page)
  await page.route('**/api/threads?**',route=>{
    const second=new URL(route.request().url()).searchParams.has('cursor')
    return route.fulfill({contentType:'application/json',headers:second?{}:{'X-Next-Cursor':'older-threads'},body:JSON.stringify([{id:second?'thread-old':'thread-new',title:second?'更早的研究':'最新的研究'}])})
  })
  await page.route('**/api/reports?**',route=>{
    const second=new URL(route.request().url()).searchParams.has('cursor')
    return route.fulfill({contentType:'application/json',headers:second?{}:{'X-Next-Cursor':'older-reports'},body:JSON.stringify([{id:second?'r-old':'r-new',topic:second?'更早的报告':'最新的报告',status:'published',reviewed_at:'2026-10-20T00:00:00Z'}])})
  })
  await page.reload()
  await page.getByRole('button',{name:'加载更多研究',exact:true}).click()
  await expect(page.locator('.thread-select').filter({hasText:'更早的研究'})).toBeVisible()
  await expect(page.getByRole('button',{name:'加载更多研究',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await page.getByRole('button',{name:'加载更多已发布报告',exact:true}).click()
  await expect(page.locator('.report-list-item')).toHaveCount(2)
  await expect(page.locator('.report-list-item').filter({hasText:'更早的报告'})).toBeVisible()
  await expect(page.getByRole('button',{name:'加载更多已发布报告',exact:true})).toHaveCount(0)
})

test('successful login opens the workspace while dependency diagnostics are slow',async({page})=>{
  await simulatedAccount(page,'researcher','author',false,true)
  await expect(page.getByRole('button',{name:'正式报告',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'正式报告',exact:true}).click()
  await expect(page.locator('.report-list-item')).toBeVisible()
})
