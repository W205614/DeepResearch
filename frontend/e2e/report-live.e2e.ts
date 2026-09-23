import { expect, test, type Page } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execute = promisify(execFile)
const authorName = process.env.E2E_USERNAME
const authorPassword = process.env.E2E_PASSWORD
const authorSubject = process.env.E2E_AUTHOR_SUBJECT
const reviewerName = process.env.E2E_REVIEWER_USERNAME
const reviewerPassword = process.env.E2E_REVIEWER_PASSWORD
const reviewerSubject = process.env.E2E_REVIEWER_SUBJECT

async function login(page: Page, username: string, password: string) {
  await page.goto('/')
  await page.getByRole('button', { name: '登录并开始研究' }).click()
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(password)
  await page.locator('#kc-login').click()
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
  const token = await page.evaluate(() => sessionStorage.getItem('dr-token'))
  expect(token).toBeTruthy()
  return { Authorization: `Bearer ${token}` }
}

async function sql(statement: string) {
  await execute('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1',
    '-U', 'deepresearch', '-d', 'deepresearch', '-c', statement], { cwd: '..' })
}

test('real OIDC accounts review, publish, read and withdraw a frozen report', async ({ browser }) => {
  test.skip(!authorName || !authorPassword || !authorSubject || !reviewerName || !reviewerPassword || !reviewerSubject,
    'The enterprise runner provisions two temporary Keycloak accounts.')
  test.setTimeout(150_000)
  const authorContext = await browser.newContext()
  const reviewerContext = await browser.newContext()
  const author = await authorContext.newPage()
  const reviewer = await reviewerContext.newPage()
  let reportId = ''
  let threadId = ''
  let cleanupHeaders: Record<string, string> | null = null
  const runId = randomUUID().replaceAll('-', '')
  const docId = randomUUID().replaceAll('-', '')
  const chunkId = randomUUID().replaceAll('-', '')
  try {
    const authorHeaders = await login(author, authorName!, authorPassword!)
    const workspace = authorSubject!
    const shared = { ...authorHeaders, 'X-Workspace-ID': workspace }
    cleanupHeaders = shared
    await author.getByRole('button', { name: '工作台设置' }).click()
    await author.getByLabel('已注册用户的账号 ID').fill(reviewerSubject!)
    await author.getByLabel('工作空间角色').selectOption('admin')
    await author.getByRole('button', { name: '添加或更新成员' }).click()
    await expect(author.getByRole('status').filter({ hasText: '成员已更新' })).toBeVisible()
    const createdThread = await author.request.post('/api/threads', {
      headers: shared, data: { title: '审核真实链路验收' },
    })
    expect(createdThread.status()).toBe(201)
    threadId = (await createdThread.json()).id
    // A deterministic completed Agent result exercises real OIDC, Java, PostgreSQL and UI review
    // without charging a model or treating generated fixture text as a researched fact.
    const sources = JSON.stringify([{ id: 'L-1', kind: 'local', title: '验收资料', text: '冻结证据原文',
      locator: '第 2 页', chunk_id: chunkId, document_id: docId, document_hash: 'fixture-hash', index_version: 1 }])
      .replaceAll("'", "''")
    await sql(`INSERT INTO documents(id,user_id,name,hash,status,index_version) VALUES('${docId}','${workspace}','验收资料','fixture-hash','ready',1); ` +
      `INSERT INTO chunks(id,document_id,user_id,text) VALUES('${chunkId}','${docId}','${workspace}','冻结证据原文'); ` +
      `INSERT INTO runs(id,user_id,thread_id,topic,mode,status,created_at,updated_at,client_request_id,created_by,data_policy,deadline_at,report,sources,validation) ` +
      `VALUES('${runId}','${workspace}','${threadId}','审核真实链路验收','deep','completed',now()::text,now()::text,'${runId}','${authorSubject}','internal',9999999999,'# 冻结报告 [L-1]\\n\\n未回答：长期效果','${sources}','{"quality":"partial","checked_claims":2,"supported_claims":1,"removed_claims":1,"unanswered_questions":["长期效果"]}');`)
    await author.reload()
    await author.locator('.thread-select').filter({ hasText: '审核真实链路验收' }).click()
    await expect(author.getByRole('button', { name: '提交人工审核' })).toBeVisible()
    await author.getByRole('button', { name: '提交人工审核' }).click()
    await expect(author.locator('.report-list-item').filter({ hasText: '审核真实链路验收' })).toBeVisible()
    expect((await (await author.request.get('/api/reports/pending', { headers: shared })).json())).toHaveLength(1)
    const pending = await author.request.get('/api/reports', { headers: shared })
    expect(await pending.json()).toEqual([])

    const reviewerHeaders = await login(reviewer, reviewerName!, reviewerPassword!)
    await reviewer.getByLabel('切换工作空间').selectOption(workspace)
    const reviewShared = { ...reviewerHeaders, 'X-Workspace-ID': workspace }
    expect((await reviewer.request.get(`/api/threads/${threadId}/report`, { headers: reviewShared })).status()).toBe(200)
    await reviewer.getByRole('button', { name: '正式报告', exact: true }).click()
    await expect(reviewer.getByRole('heading', { name: '待审核' })).toBeVisible()
    await reviewer.locator('.report-list-item').filter({ hasText: '审核真实链路验收' }).click()
    await expect(reviewer.getByRole('button', { name: '批准发布' })).toBeVisible()
    await expect(reviewer.getByRole('button', { name: '批准发布' })).toBeDisabled()
    await reviewer.getByRole('textbox', { name: '审核意见（部分结果批准时必填）' }).fill('仅发布现有证据，长期效果尚未回答')
    await reviewer.getByRole('button', { name: '批准发布' }).click()
    await expect(reviewer.getByText('正式版本 · 部分结果', { exact: true })).toBeVisible()
    await expect(reviewer.getByText('批准说明：仅发布现有证据，长期效果尚未回答')).toBeVisible()
    const listed = await (await reviewer.request.get('/api/reports', { headers: reviewShared })).json()
    reportId = listed[0].id
    const before = await (await reviewer.request.get(`/api/reports/${reportId}`, { headers: reviewShared })).json()
    expect(before.report_markdown).toContain('# 冻结报告 [L-1]')
    expect(before.validation.quality).toBe('partial')
    expect(before.validation.unanswered_questions).toEqual(['长期效果'])
    expect(before.sources[0].original_available).toBe(true)
    expect((await reviewer.request.get(`/api/reports/${reportId}/download`, { headers: reviewShared })).status()).toBe(200)
    await sql(`UPDATE runs SET report='# 被修改的任务内容 [L-1]' WHERE id='${runId}'; ` +
      `UPDATE documents SET index_version=2 WHERE id='${docId}';`)
    const frozen = await (await reviewer.request.get(`/api/reports/${reportId}`, { headers: reviewShared })).json()
    expect(frozen.report_markdown).toBe(before.report_markdown)
    expect(frozen.content_sha256).toBe(before.content_sha256)
    expect(frozen.sources[0].locator).toBe('第 2 页')
    expect(frozen.sources[0].original_available).toBe(false)
    await expect(reviewer.locator('.report-list-item .green-pill')).toHaveCount(1)
    await reviewer.locator('.report-list-item .green-pill').click()
    await expect(reviewer.getByText('原文当前不可访问')).toBeVisible()
    await reviewer.getByRole('textbox', { name: '撤回原因' }).fill('原件版本已更新')
    await reviewer.getByRole('button', { name: '撤回正式版本' }).click()
    await expect(reviewer.getByText('撤回原因：原件版本已更新')).toBeVisible()
    expect(await (await author.request.get('/api/reports', { headers: shared })).json()).toEqual([])
    const audit = await (await reviewer.request.get(`/api/workspaces/${workspace}/audit`, { headers: reviewShared })).json()
    expect(audit.map((entry: { action: string }) => entry.action)).toEqual(expect.arrayContaining([
      'report.submit', 'report.approve', 'report.withdraw',
    ]))
    await author.getByRole('button', { name: '工作台设置' }).click()
    author.once('dialog', dialog => dialog.accept())
    await author.getByRole('button', { name: `移除成员 ${reviewerSubject}` }).click()
    await expect(author.getByRole('status').filter({ hasText: '成员已移除' })).toBeVisible()
    expect((await reviewer.request.get(`/api/threads/${threadId}/report`, { headers: reviewShared })).status()).toBe(403)
    const removalAudit = await (await author.request.get(`/api/workspaces/${workspace}/audit`, { headers: shared })).json()
    expect(removalAudit.map((entry: { action: string }) => entry.action)).toContain('membership.remove')
  } finally {
    if (reportId && cleanupHeaders) await author.request.delete(`/api/reports/${reportId}`, { headers: cleanupHeaders })
    await authorContext.close()
    await reviewerContext.close()
    // The enterprise runner removes both temporary identities and all fixture rows.
  }
})
