import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { expect, test, type Page } from '@playwright/test'

const workspace = process.env.E2E_AUTHOR_SUBJECT!
const narrow = process.env.E2E_PRODUCT_NARROW === '1'
const narrowViewport = {width:390,height:844}
const desktopViewport = {width:1440,height:900}
const workingViewport = narrow ? narrowViewport : desktopViewport

test.use({viewport:workingViewport})
type Role = 'author'|'reviewer'|'viewer'
const accounts = {
  author: [process.env.E2E_USERNAME, process.env.E2E_PASSWORD],
  reviewer: [process.env.E2E_REVIEWER_USERNAME, process.env.E2E_REVIEWER_PASSWORD],
  viewer: [process.env.E2E_VIEWER_USERNAME, process.env.E2E_VIEWER_PASSWORD],
}
const pg = 'PostgreSQL 的全文检索怎么做？'
const milvus = '比较两条路线按项目筛选和删除过期笔记的能力组合。'
const editedQuery = 'Milvus 官方 Filtered Search Delete Entities'
const memory = '峰值内存的实测结果是多少？'
const budget = '冷启动实测结果是什么？'
const compareTopic = '请全面研究 2025 年中国市场，比较 PaperBridge 试点方案与复测记录，说明人数口径的差异和待验项。'

test.beforeEach(() => {
  test.skip(process.env.E2E_PRODUCT_FLOW !== '1', 'Run against the dedicated fixed-evidence product acceptance stack.')
  expect(process.env.E2E_COMPOSE_PROJECT).toBe('dr-product-five')
  expect(new URL(process.env.E2E_BASE_URL!).origin).toBe('http://127.0.0.1:22880')
  test.setTimeout(240_000)
})

async function login(page:Page, role:Role) {
  page.setDefaultTimeout(20_000)
  await page.goto('/')
  await page.getByRole('button', {name:'登录并开始研究'}).click()
  await page.locator('#username').fill(accounts[role][0]!)
  await page.locator('#password').fill(accounts[role][1]!)
  await page.locator('#kc-login').click()
  await expect(page.getByRole('button', {name:/退出/})).toBeVisible({timeout:30_000})
  const Authorization = 'Bearer ' + await page.evaluate(() => sessionStorage.getItem('dr-token'))
  return { Authorization, 'X-Workspace-ID':workspace }
}

async function openNavigation(page:Page) {
  const mobile = page.getByRole('button', {name:'打开导航'})
  if (await mobile.isVisible() && await mobile.getAttribute('aria-expanded') !== 'true') await mobile.click()
}

async function openReports(page:Page) {
  await openNavigation(page)
  await page.getByRole('button', {name:'正式报告', exact:true}).click()
  if (await page.getByRole('button', {name:'返回报告列表', exact:true}).isVisible())
    await page.getByRole('button', {name:'返回报告列表', exact:true}).click()
}

async function runStatus(page:Page, id:string, headers:Record<string,string>, terminalStatus:string) {
  let run
  await expect.poll(async () => {
    const response = await page.request.get('/api/research/runs/' + id, {headers})
    expect(response.ok()).toBeTruthy()
    run = await response.json()
    return run.status
  }, {timeout:25_000}).toBe(terminalStatus)
  return run!
}

function postResponse(page:Page, suffix:string) {
  return page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(suffix))
}

const coverage = (page:Page, requirement:string) => page.getByRole('region', {name:'答题覆盖'}).locator('li').filter({hasText:requirement})

test('product flow public research, focused merge, retries and failure boundaries', async ({page}) => {
  const headers = await login(page, 'author')
  await page.getByLabel('资料范围', {exact:true}).selectOption('public')
  await page.getByLabel('研究模式').selectOption('quick')
  await page.getByText('资料处理与额度', {exact:false}).click()
  await expect(page.getByText(/本次最多.*次调用/)).toBeVisible()
  await expect(page.getByText(/公开网页片段可能由配置的搜索或模型服务处理/)).toBeVisible()
  await page.getByLabel('研究主题', {exact:true}).fill(
    '单机约束下比较 PostgreSQL 与 Milvus。' + [pg, milvus, memory, budget].join(''))
  const started = postResponse(page, '/api/research/runs')
  await page.getByRole('button', {name:'开始研究', exact:true}).click()
  const response = await started
  expect(response.status()).toBe(202)
  const first = await runStatus(page, (await response.json()).id, headers, 'completed')
  await expect(coverage(page, pg)).toContainText('已回答')
  await expect(coverage(page, milvus)).toContainText('证据不足')
  await coverage(page, pg).getByRole('button', {name:'查看依据 1'}).click()
  await expect(page.locator('[data-verified-claim]:focus')).toContainText('GIN 索引')

  const proposals = coverage(page, milvus)
  const supplements: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/supplements')) supplements.push(request.url())
  })
  await proposals.getByRole('button', {name:'仅补查此项'}).click()
  await expect(proposals.getByLabel('检索词')).toHaveValue('PostgreSQL 与 Milvus ' + milvus)
  await proposals.getByRole('button', {name:'取消', exact:true}).click()
  await expect(proposals.getByLabel('检索词')).toHaveCount(0)
  expect(supplements).toHaveLength(0)
  await proposals.getByRole('button', {name:'仅补查此项'}).click()
  await proposals.getByLabel('检索词').fill(editedQuery)
  let holdBackgroundStatus = true
  const detailRoute = /\/api\/research\/runs\/[^/?]+$/
  await page.route(detailRoute, async route => {
    if (!holdBackgroundStatus || route.request().method() !== 'GET' || route.request().url().endsWith('/' + first.id)) {
      await route.continue()
      return
    }
    const actual = await route.fetch()
    if (!actual.ok()) { await route.fulfill({response:actual}); return }
    await route.fulfill({response:actual, json:{...(await actual.json()),status:'running',report:'',sources:[],validation:{}}})
  })
  const post = postResponse(page, '/supplements')
  await proposals.getByRole('button', {name:'开始补查', exact:true}).click()
  const submitted = await post
  expect(submitted.status()).toBe(202)
  const child = await submitted.json()
  const replay = await page.request.post(new URL(submitted.url()).pathname, {headers, data:submitted.request().postDataJSON()})
  expect(replay.status()).toBe(202)
  expect((await replay.json()).id).toBe(child.id)
  const combined = await runStatus(page, child.id, headers, 'completed')
  expect(combined.related_run_id).toBe(first.id)
  expect(combined.data_policy).toBe('public')
  expect(combined.sources.map((source:{kind:string}) => source.kind)).toEqual(['web','web'])
  expect(combined.validation.supplement).toMatchObject({merged:true, previous_status:'insufficient'})
  const history = page.getByLabel('本会话的历史研究')
  await history.locator('summary').click()
  await expect(history.locator('.conversation-response-heading')).toContainText('部分结果')
  await history.getByRole('button', {name:/查看来源/}).click()
  await expect(page.locator('.current-turn')).toContainText(first.topic)
  await page.getByLabel('继续追问').fill('确认后台状态同步')
  await expect(page.getByRole('button', {name:'发送追问'})).toBeDisabled()
  holdBackgroundStatus = false
  await expect(page.getByRole('button', {name:'发送追问'})).toBeEnabled()
  await expect(page.locator('.current-turn')).toContainText(first.topic)
  await page.getByLabel('继续追问').clear()
  await page.unroute(detailRoute)
  if (await history.getAttribute('open') === null) await history.locator('summary').click()
  await history.getByRole('button', {name:'查看来源 2'}).click()
  await expect(page.getByText(/补查已合入草稿/)).toBeVisible()
  await expect(coverage(page, pg)).toContainText('已回答')
  await expect(coverage(page, milvus)).toContainText('部分回答')
  await expect(coverage(page, milvus)).toContainText('缺少 PostgreSQL 按项目过滤和删除的证据')
  expect(combined.report).not.toContain('笔记应用可以按标签归档')
  await expect(coverage(page, memory)).toContainText('证据不足')
  const eventStream = await page.request.get('/api/research/runs/' + child.id + '/events', {headers})
  expect(eventStream.ok()).toBeTruthy()
  const events = (await eventStream.text()).split(/\r?\n/).filter(line => line.startsWith('data:'))
    .map(line => JSON.parse(line.slice(5)))
  expect(events.find(event => event.type === 'plan')?.data.queries).toEqual([editedQuery])
  expect(events.find(event => event.type === 'validation')?.data.checked_claims).toBe(1)
  expect(events.find(event => event.type === 'validation')?.data.requirements).toHaveLength(1)
  await page.getByRole('button', {name:'查看原研究',exact:true}).click()
  await expect(coverage(page, milvus)).toContainText('证据不足')
  const reloaded = await runStatus(page, first.id, headers, 'completed')
  expect(reloaded.report).toBe(first.report)
  expect(reloaded.validation).toEqual(first.validation)

  const memoryItem = coverage(page, memory)
  await memoryItem.getByRole('button', {name:'仅补查此项'}).click()
  await memoryItem.getByLabel('检索词').fill('仅查找不存在的公开文档')
  const noSources = postResponse(page, '/supplements')
  await memoryItem.getByRole('button', {name:'开始补查', exact:true}).click()
  const empty = await runStatus(page, (await (await noSources).json()).id, headers, 'insufficient')
  expect(empty.validation.retrieval_outcomes).toContainEqual({source:'web', outcome:'empty'})
  expect(empty.report).toContain('联网搜索未返回可验证的来源')
  await expect(page.getByRole('heading', {name:'补查证据不足', exact:true})).toBeVisible()
  await expect(page.getByText(/检索服务不可用/)).toHaveCount(0)
  await page.getByRole('button', {name:'查看原研究', exact:true}).click()

  await memoryItem.getByRole('button', {name:'补充资料'}).click()
  await memoryItem.getByLabel('检索词').fill('仅查找不存在的公开文档')
  await page.getByLabel('公开来源 URL').fill('https://example.com/product-fixture/unavailable')
  const unreadable = postResponse(page, '/supplements')
  await page.getByRole('button', {name:'使用此来源补查'}).click()
  const failedRead = await runStatus(page, (await (await unreadable).json()).id, headers, 'insufficient')
  expect(failedRead.validation.supplement.merged).toBe(false)
  expect(failedRead.validation.requirements.map((item:{text:string}) => item.text)).toEqual([memory])
  expect(failedRead.validation.claim_links).toEqual([])
  expect(failedRead.sources).toEqual([])
  expect(failedRead.report).toBe(
    '# 补查证据不足\n\n所选问题：' + memory + '\n\n还缺：缺少实测材料\n\n' +
    '原因：联网搜索未返回可验证的来源。 1 个候选网页未取得可读正文（来源正文不可访问（HTTP 404）），已排除。')
  await expect(page.getByText(/来源正文不可访问（HTTP 404）/)).toBeVisible()
  await expect(page.getByText(/检索服务不可用|部分检索服务发生故障/)).toHaveCount(0)
  await expect(page.getByText('研究来源', {exact:true})).toHaveCount(0)
  await page.getByRole('button', {name:'查看原研究',exact:true}).click()

  await memoryItem.getByRole('button', {name:'仅补查此项'}).click()
  await memoryItem.getByLabel('检索词').fill('复现检索服务不可用')
  const outage = postResponse(page, '/supplements')
  await memoryItem.getByRole('button', {name:'开始补查',exact:true}).click()
  const unavailable = await runStatus(page, (await (await outage).json()).id, headers, 'failed')
  expect(unavailable.validation.retrieval_failed).toBe(true)
  expect(unavailable.validation.retrieval_outcomes).toContainEqual({source:'web', outcome:'error'})
  await expect(page.getByRole('heading', {name:'检索服务不可用', exact:true})).toBeVisible()
  await page.getByRole('button', {name:'查看原研究',exact:true}).click()
  const afterFailures = await runStatus(page, first.id, headers, 'completed')
  expect(afterFailures.report).toBe(first.report)
  expect(afterFailures.validation).toEqual(first.validation)

  const exhausted = postResponse(page, '/supplements')
  await coverage(page, budget).getByRole('button', {name:'仅补查此项'}).click()
  await coverage(page, budget).getByRole('button', {name:'开始补查', exact:true}).click()
  const failedBudget = await runStatus(page, (await (await exhausted).json()).id, headers, 'failed')
  expect(failedBudget.error_info.code).toBe('budget_exhausted')
  await expect(page.getByText(/原结果仍可查看/)).toBeVisible()
  await page.getByRole('button', {name:'查看原研究',exact:true}).click()
  await coverage(page, memory).getByRole('button', {name:'调整范围'}).click()
  await expect(page.getByRole('heading', {name:'先确认这次研究的范围'})).toBeVisible()
  await page.getByRole('button', {name:'取消这次研究'}).click()
  await openNavigation(page)
  await page.getByRole('button', {name:'新建研究',exact:true}).click()
  await openNavigation(page)
  const parent = page.locator('.thread-select').filter({hasText:'比较 PostgreSQL 与 Milvus'})
  await parent.click()
  await page.getByRole('button', {name:'查看原研究',exact:true}).click()
  const publishedDraft = postResponse(page,'/publication')
  await page.getByRole('button',{name:'提交人工审核'}).click()
  const originalResponse = await publishedDraft
  expect(originalResponse.status()).toBe(201)
  const original = await originalResponse.json()
  const added = await page.request.put('/api/workspaces/' + workspace + '/members', {
    headers,data:{subject:process.env.E2E_REVIEWER_SUBJECT,role:'admin'}})
  expect(added.ok()).toBeTruthy()
  const reviewerContext = await page.context().browser()!.newContext({viewport:workingViewport})
  try {
    const reviewer = await reviewerContext.newPage()
    await login(reviewer,'reviewer')
    await openNavigation(reviewer)
    await reviewer.getByLabel('切换工作空间').selectOption(workspace)
    await openReports(reviewer)
    await reviewer.locator('.report-list-item').filter({hasText:'比较 PostgreSQL 与 Milvus'}).click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已核对已有结论；Milvus 与实测项仍待补查。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.locator('.report-toolbar .pill')).toContainText('正式版本')
    await openReports(page)
    await page.locator('.report-list-item').filter({hasText:'比较 PostgreSQL 与 Milvus'}).click()
    const revision = postResponse(page, '/revisions')
    await coverage(page, milvus).getByRole('button', {name:'仅补查此项'}).click()
    await expect(coverage(page, milvus).getByLabel('检索词')).toHaveValue('PostgreSQL 与 Milvus ' + milvus)
    await coverage(page, milvus).getByRole('button', {name:'开始补查', exact:true}).click()
    const job = await revision
    expect(job.status()).toBe(202)
    await runStatus(page, (await job.json()).run_id, headers, 'completed')
    await expect(page.getByText(/补查已合入草稿/)).toBeVisible()
    const revisedDraft = postResponse(page,'/publication')
    await page.getByRole('button',{name:'提交人工审核'}).click()
    const draft = await revisedDraft
    expect(draft.status()).toBe(201)
    const version = await draft.json()
    expect(version.parent_report_id).toBe(original.id)
    await openReports(reviewer)
    await reviewer.locator('.report-list-item').filter({hasText:'比较 PostgreSQL 与 Milvus'}).filter({hasText:'待审核'}).click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已核对两种数据库结论；实测项待补足。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.locator('.report-toolbar .pill')).toContainText('正式版本')
    await reviewer.getByRole('button',{name:'重新获取公开来源'}).click()
    await expect(reviewer.getByText(/PostgreSQL 手册.*正文已变化/)).toBeVisible()
    await expect(reviewer.getByText(/Milvus 手册.*当前无法访问/)).toBeVisible()
    await expect(reviewer.getByRole('heading',{name:'依赖此来源的结论'})).toHaveCount(2)
    const recheck = await page.request.get('/api/reports/' + version.id,{headers})
    expect((await recheck.json()).content_sha256).toBe(version.content_sha256)
  } finally { await reviewerContext.close() }
})

test('product flow internal evidence, revision, separate reviewer and viewer summary', async ({browser}) => {
  const authorContext = await browser.newContext({viewport:workingViewport})
  const reviewerContext = await browser.newContext({viewport:workingViewport})
  const viewerContext = await browser.newContext({viewport:narrow ? desktopViewport : narrowViewport})
  const author = await authorContext.newPage()
  const reviewer = await reviewerContext.newPage()
  const viewer = await viewerContext.newPage()
  try {
    const headers = await login(author, 'author')
    for (const [subject,role] of [[process.env.E2E_REVIEWER_SUBJECT,'admin'],[process.env.E2E_VIEWER_SUBJECT,'viewer']]) {
      const added = await author.request.put('/api/workspaces/' + workspace + '/members', {headers,data:{subject,role}})
      expect(added.ok()).toBeTruthy()
    }
    await login(reviewer,'reviewer')
    const viewerHeaders = await login(viewer,'viewer')
    await openNavigation(reviewer)
    await reviewer.getByLabel('切换工作空间').selectOption(workspace)
    await openNavigation(viewer)
    await viewer.getByLabel('切换工作空间').selectOption(workspace)

    const tag = randomUUID().slice(0,6)
    const names = ['PaperBridge-试点方案-' + tag + '.md', 'PaperBridge-复测记录-' + tag + '.md']
    const file = (name:string, body:string) => ({name,mimeType:'text/markdown',buffer:Buffer.from(body)})
    await openNavigation(author)
    await author.getByRole('button',{name:/^本地资料库/}).click()
    await author.locator('input[type=file][multiple]').setInputFiles([
      file(names[0]!,'# PaperBridge 试点方案\n\n试点方案记载完成名单只要求提交初始记录，未要求通过全部检查。'),
      file(names[1]!,'# PaperBridge 复测记录\n\n复测记录记载完成名单只纳入通过全部检查的人员。'),
    ])
    for (const name of names) await expect(author.locator('.list-card').filter({hasText:name})).toContainText('可检索',{timeout:40_000})

    await openNavigation(author)
    await author.getByRole('button',{name:'新建研究',exact:true}).click()
    await author.getByLabel('资料范围',{exact:true}).selectOption('internal')
    await author.getByLabel('研究主题',{exact:true}).fill(compareTopic)
    await author.getByRole('button',{name:'开始研究',exact:true}).click()
    const form = author.locator('.research-scope')
    await expect(form.getByLabel('时间范围')).toHaveValue('2025 年')
    await expect(form.getByLabel('地区范围')).toHaveValue('中国市场')
    await expect(form.getByLabel('研究对象')).toHaveValue('PaperBridge 试点方案与复测记录')
    await expect(form.getByLabel('交付标准')).not.toHaveValue('')
    await form.locator('summary').click()
    await expect(form.getByText(/匹配的内部片段.*模型服务处理/)).toBeVisible()
    const confirmed = postResponse(author, '/confirm')
    await form.getByRole('button',{name:'确认并开始研究'}).click()
    const first = await runStatus(author, (await (await confirmed).json()).id, headers, 'completed')
    expect(first.sources).toHaveLength(2)
    await expect(coverage(author,'两份资料的完成名单口径')).toContainText('部分回答')
    await author.getByRole('button',{name:'核对这条结论'}).first().click()
    const compare = author.getByRole('dialog',{name:'核对结论'})
    await expect(compare).toContainText('已核验的口径差异')
    await expect(compare).toContainText('提交初始记录')
    await expect(compare).toContainText('通过全部检查')
    await expect(compare.getByText('资料版本 1')).toHaveCount(2)
    await author.keyboard.press('Escape')

    const submit = postResponse(author,'/publication')
    await author.getByRole('button',{name:'提交人工审核'}).click()
    const originalResponse = await submit
    expect(originalResponse.status()).toBe(201)
    const original = await originalResponse.json()
    await openReports(reviewer)
    await reviewer.locator('.report-list-item').filter({hasText:compareTopic}).click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已复核两份原文，仍需补充冷启动测量。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.locator('.report-toolbar .pill')).toContainText('正式版本')

    await openNavigation(author)
    await author.getByRole('button',{name:/^本地资料库/}).click()
    const retest = author.locator('.list-card').filter({hasText:names[1]!})
    await retest.getByRole('button',{name:'替换',exact:true}).click({noWaitAfter:true})
    await author.locator('input[type=file]:not([multiple])').setInputFiles(
      file(names[1]!,'# PaperBridge 复测记录\n\n复测记录记载完成名单只纳入通过全部检查并提交日志的人员。'))
    await expect(retest).toContainText('版本 2',{timeout:40_000})
    await expect(retest).toContainText('可检索')
    await openReports(author)
    await author.locator('.report-list-item').filter({hasText:compareTopic}).click()
    const impact = author.getByRole('region',{name:'报告版本与资料影响'})
    await expect(impact).toContainText('待复核结论',{timeout:30_000})
    const revision = postResponse(author,'/revisions')
    await impact.getByRole('button',{name:'依据当前资料发起修订'}).click()
    const revised = await revision
    expect(revised.status()).toBe(202)
    await impact.getByRole('button',{name:'查看修订进度',exact:true}).click()
    await runStatus(author,(await revised.json()).run_id,headers,'completed')
    const revisedSubmit = postResponse(author,'/publication')
    await author.getByRole('button',{name:'提交人工审核'}).click()
    const next = await revisedSubmit
    expect(next.status()).toBe(201)
    const pending = await next.json()
    expect(pending.parent_report_id).toBe(original.id)

    await openReports(reviewer)
    await reviewer.locator('.report-list-item').filter({hasText:compareTopic}).filter({hasText:'待审核'}).click()
    await reviewer.getByRole('textbox',{name:/审核意见/}).fill('已核对新版资料和仍未测量的条件。')
    await reviewer.getByRole('button',{name:'批准发布',exact:true}).click()
    await expect(reviewer.locator('.report-toolbar .pill')).toContainText('正式版本')

    await openReports(viewer)
    await viewer.locator('.report-list-item').filter({hasText:compareTopic}).first().click()
    await expect(viewer.getByRole('region',{name:'答题覆盖'})).toContainText('部分回答')
    await expect(viewer.getByRole('button',{name:'仅补查此项'})).toHaveCount(0)
    const downloaded = viewer.waitForEvent('download')
    await viewer.getByRole('button',{name:'下载决策摘要'}).click()
    const download = await downloaded
    const markdown = await readFile((await download.path())!, 'utf8')
    expect(markdown).toContain('第 2 版')
    expect(markdown).toContain('并提交日志')
    expect(markdown).toContain('资料版本 2')
    expect(markdown).toContain('冷启动')
    expect(markdown).toContain('适用条件与局限')
    expect(await viewer.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const retained = await viewer.request.get('/api/reports/' + original.id,{headers:viewerHeaders})
    expect((await retained.json()).content_sha256).toBe(original.content_sha256)
    await author.request.delete('/api/workspaces/' + workspace + '/members/' + process.env.E2E_VIEWER_SUBJECT,{headers})
    const revoked = await viewer.request.get('/api/reports/' + pending.id + '/download?format=decision',{headers:viewerHeaders})
    expect([403,404]).toContain(revoked.status())
  } finally {
    await Promise.all([authorContext.close(),reviewerContext.close(),viewerContext.close()])
  }
})
