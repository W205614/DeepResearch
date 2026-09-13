import { expect, test } from '@playwright/test'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const username = process.env.E2E_USERNAME
const password = process.env.E2E_PASSWORD

test('anonymous users see OIDC-only entry and protected APIs reject access', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '让研究有据可循。' })).toBeVisible()
  await expect(page.getByRole('button', { name: '登录并开始研究' })).toBeVisible()

  const config = await page.request.get('/api/auth/config')
  await expect(config).toBeOK()
  await expect(config.json()).resolves.toMatchObject({ mode: 'oidc' })
  const protectedThreads = await page.request.get('/api/threads')
  expect(protectedThreads.status()).toBe(401)
})

const liveCases = JSON.parse(readFileSync(resolve('../eval/live_research_cases.json'), 'utf8'))
for (const item of liveCases) {
  test(`live research workflow: ${item.id}`, async ({ page }) => {
    test.skip(process.env.E2E_LIVE !== '1', 'Explicit paid-model acceptance only')
    test.setTimeout(720_000)
    await page.goto('/')
    await page.getByRole('button', { name: '登录并开始研究' }).click()
    await page.locator('#username').fill(username!)
    await page.locator('#password').fill(password!)
    await page.locator('#kc-login').click()
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
    const token = await page.evaluate(() => sessionStorage.getItem('dr-token'))
    const headers = { Authorization: `Bearer ${token}` }
    let documentId = ''
    let threadId = ''
    try {
      if (item.id === 'live-postgres') {
        await page.getByRole('button', { name: '本地资料库' }).click()
        await page.locator('input[type=file]').setInputFiles({ name: 'acceptance-note.txt', mimeType: 'text/plain',
          buffer: Buffer.from('验收备注：本项目使用应用层工作空间权限。数据库行级安全是另一层防线，需要正确配置运行角色；不能把尚未实施的能力描述为已经具备。') })
        await expect.poll(async () => {
          const docs = await (await page.request.get('/api/documents', { headers })).json()
          const doc = docs.find((d: any) => d.name === 'acceptance-note.txt')
          documentId = doc?.id || ''
          return doc?.status
        }, { timeout: 90_000 }).toBe('ready')
      }
      await page.getByRole('button', { name: '新建研究' }).click()
      await page.getByLabel('资料范围').selectOption(documentId ? 'internal' : 'public')
      await page.getByLabel('研究模式', { exact: true }).selectOption('deep')
      await page.getByLabel('研究主题').fill(item.question + (documentId ? ' 请同时结合本地验收备注说明本项目的边界，并引用该资料。' : ''))
      const responsePromise = page.waitForResponse(r => r.url().endsWith('/api/research/runs') && r.request().method() === 'POST')
      await page.getByRole('button', { name: '开始研究', exact: true }).click()
      const created = await responsePromise
      expect(created.status()).toBe(202)
      const run = await created.json()
      threadId = run.thread_id
      if (item.id === 'live-postgres') {
        expect((await page.request.post(`/api/research/runs/${run.id}/cancel`, { headers })).ok()).toBeTruthy()
        await expect.poll(async () => (await (await page.request.get(`/api/research/runs/${run.id}`, { headers })).json()).status).toBe('cancelled')
        expect((await page.request.post(`/api/research/runs/${run.id}/resume`, { headers })).status()).toBe(202)
        await page.reload()
      }
      let result: any
      await expect.poll(async () => {
        const response = await page.request.get(`/api/research/runs/${run.id}`, { headers })
        expect(response.status()).toBe(200)
        result = await response.json()
        return result.status
      }, { timeout: 600_000, intervals: [2000] }).toBe('completed')
      const stream = await page.request.get(`/api/research/runs/${run.id}/events`, { headers })
      const events = (await stream.text()).split('\n').filter(line => line.startsWith('data: {'))
        .map(line => JSON.parse(line.slice(6))).filter(event => event.type)
      expect(events.filter(e => e.type === 'done')).toHaveLength(1)
      const replay = await page.request.get(`/api/research/runs/${run.id}/events?after=${events[0].id}`, { headers })
      expect(await replay.text()).not.toContain(`id: ${events[0].id}\n`)
      expect((await page.request.get(`/api/research/runs/${run.id}/report`, { headers })).ok()).toBeTruthy()
      await page.reload()
      await expect(page.locator('.report-card').first()).toBeVisible({ timeout: 15000 })
      const output = resolve('../.cache/eval/live-browser')
      mkdirSync(output, { recursive: true })
      writeFileSync(resolve(output, `${item.id}.json`), JSON.stringify({ ...result,
        nodes: events.filter(e => e.type === 'node_end').map(e => e.data.node) }))
      expect(result.sources.some((s: any) => s.kind === 'web' && s.access === 'fulltext')).toBeTruthy()
      if (documentId) expect(result.sources.some((s: any) => s.document_id === documentId)).toBeTruthy()
    } finally {
      if (documentId) {
        expect((await page.request.delete(`/api/documents/${documentId}`, { headers })).ok()).toBeTruthy()
      }
      if (threadId) {
        expect((await page.request.delete(`/api/threads/${threadId}`, { headers })).ok()).toBeTruthy()
      }
    }
  })
}

test('temporary enterprise user completes PKCE login and reaches the protected workspace', async ({ page }) => {
  test.skip(!username || !password, 'Set E2E_USERNAME and E2E_PASSWORD to run the live OIDC path.')
  await page.goto('/')
  await page.getByRole('button', { name: '登录并开始研究' }).click()
  await expect(page.locator('#username')).toBeVisible()
  await page.locator('#username').fill(username!)
  await page.locator('#password').fill(password!)
  await page.locator('#kc-login').click()

  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '新建研究' })).toBeVisible()
  await expect(page.locator('textarea[aria-label="研究主题"]')).toBeVisible()

  await page.getByRole('button', { name: '工作台设置' }).click()
  await expect(page.getByRole('heading', { name: '你的研究工作空间。' })).toBeVisible()
  await expect(page.getByText('本地研究指标')).toBeVisible()
})

test('document lifecycle and viewer authorization with real adapters', async ({ page }) => {
  test.skip(process.env.E2E_DOCUMENTS !== '1', 'Explicit real embedding acceptance only')
  test.setTimeout(120_000)
  await page.goto('/')
  await page.getByRole('button', { name: '登录并开始研究' }).click()
  await page.locator('#username').fill(username!)
  await page.locator('#password').fill(password!)
  await page.locator('#kc-login').click()
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
  const token = await page.evaluate(() => sessionStorage.getItem('dr-token'))
  const headers = { Authorization: `Bearer ${token}` }
  let documentId = ''
  try {
    await page.getByRole('button', { name: '本地资料库' }).click()
    await page.locator('input[type=file]').setInputFiles({ name: 'lifecycle.txt', mimeType: 'text/plain',
      buffer: Buffer.from('功能验收资料：工作空间成员权限在后端校验。删除资料后，不应在知识库检索结果中出现。') })
    await expect.poll(async () => {
      const docs = await (await page.request.get('/api/documents', { headers })).json()
      const doc = docs.find((d: any) => d.name === 'lifecycle.txt')
      documentId = doc?.id || ''
      return doc?.status
    }, { timeout: 90_000 }).toBe('ready')
    const found = await (await page.request.post('/api/documents/search', { headers, data: { query: '工作空间成员权限' } })).json()
    expect(found.some((d: any) => d.document_id === documentId)).toBeTruthy()
  } finally {
    if (documentId) expect((await page.request.delete(`/api/documents/${documentId}`, { headers })).ok()).toBeTruthy()
  }
  const found = await (await page.request.post('/api/documents/search', { headers, data: { query: '工作空间成员权限' } })).json()
  expect(found.some((d: any) => d.document_id === documentId)).toBeFalsy()
  const workspaces = await (await page.request.get('/api/workspaces', { headers })).json()
  const members = await (await page.request.get(`/api/workspaces/${workspaces[0].id}/members`, { headers })).json()
  expect((await page.request.put(`/api/workspaces/${workspaces[0].id}/members`, { headers,
    data: { subject: members[0].subject, role: 'viewer' } })).ok()).toBeTruthy()
  expect((await page.request.post('/api/research/runs', { headers,
    data: { topic: 'should be rejected', client_request_id: 'viewer-rejection' } })).status()).toBe(403)
})
