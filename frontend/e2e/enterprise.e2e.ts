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
    let threadId = ''
    try {
      await page.getByRole('button', { name: '新建研究' }).click()
      await page.getByLabel('资料范围').selectOption('public')
      await page.getByLabel('研究模式', { exact: true }).selectOption('deep')
      await page.getByLabel('研究主题').fill(item.question)
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
      const deadline = Date.now() + 600_000
      while (Date.now() < deadline) {
        const response = await page.request.get(`/api/research/runs/${run.id}`, { headers })
        expect(response.status()).toBe(200)
        result = await response.json()
        if (result.status === 'completed') break
        if (['failed', 'cancelled', 'insufficient', 'interrupted'].includes(result.status))
          throw new Error(`Research terminated as ${result.status}; reasons=${JSON.stringify(result.validation?.reasons || [])}`)
        await page.waitForTimeout(2000)
      }
      expect(result.status).toBe('completed')
      const stream = await page.request.get(`/api/research/runs/${run.id}/events`, { headers })
      const events = (await stream.text()).split('\n').filter(line => /^data:\s*\{/.test(line))
        .map(line => JSON.parse(line.slice(5).trim())).filter(event => event.type)
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
    } finally {
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

test('Java business API owns authenticated thread, preference and research lifecycle', async ({ page }) => {
  test.skip(!username || !password, 'Set E2E_USERNAME and E2E_PASSWORD to run the live OIDC path.')
  await page.goto('/')
  await page.getByRole('button', { name: '登录并开始研究' }).click()
  await page.locator('#username').fill(username!)
  await page.locator('#password').fill(password!)
  await page.locator('#kc-login').click()
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()

  const token = await page.evaluate(() => sessionStorage.getItem('dr-token'))
  const headers = { Authorization: `Bearer ${token}` }
  let threadId = ''
  let memoryId = ''
  try {
    const createdThread = await page.request.post('/api/threads', {
      headers, data: { title: 'Java 业务边界验收' },
    })
    expect(createdThread.status()).toBe(201)
    threadId = (await createdThread.json()).id
    expect((await page.request.patch(`/api/threads/${threadId}`, {
      headers, data: { title: 'Java 业务边界验收（已重命名）' },
    })).ok()).toBeTruthy()

    const createdMemory = await page.request.post('/api/memories', {
      headers, data: { content: '回答优先给出可核验的证据边界' },
    })
    expect(createdMemory.status()).toBe(201)
    memoryId = (await createdMemory.json()).id
    expect((await page.request.put(`/api/memories/${memoryId}`, {
      headers, data: { content: '回答优先说明事实、推断与证据边界' },
    })).ok()).toBeTruthy()
    const memories = await (await page.request.get('/api/memories', { headers })).json()
    expect(memories).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: memoryId, content: '回答优先说明事实、推断与证据边界' }),
    ]))

    const clientRequestId = `java-boundary-${Date.now()}`
    const request = {
      topic: '验证 Java 业务服务能够持久化、幂等调度并取消研究任务',
      data_policy: 'public', mode: 'quick', thread_id: threadId,
      client_request_id: clientRequestId,
    }
    const createdRun = await page.request.post('/api/research/runs', { headers, data: request })
    expect(createdRun.status()).toBe(202)
    const run = await createdRun.json()
    const replay = await page.request.post('/api/research/runs', { headers, data: request })
    expect(replay.status()).toBe(202)
    expect((await replay.json()).id).toBe(run.id)

    const cancelled = await page.request.post(`/api/research/runs/${run.id}/cancel`, { headers })
    expect(cancelled.status()).toBe(200)
    expect((await cancelled.json()).status).toBe('cancelled')
    const eventStream = await page.request.get(`/api/research/runs/${run.id}/events`, { headers })
    expect(eventStream.status()).toBe(200)
    expect(await eventStream.text()).toContain('"type":"cancelled"')
  } finally {
    if (memoryId) expect((await page.request.delete(`/api/memories/${memoryId}`, { headers })).ok()).toBeTruthy()
    if (threadId) expect((await page.request.delete(`/api/threads/${threadId}`, { headers })).ok()).toBeTruthy()
  }
})

test('document lifecycle and viewer authorization with real adapters', async ({ page, browser }) => {
  test.skip(process.env.E2E_DOCUMENTS !== '1', 'Explicit real embedding acceptance only')
  test.setTimeout(240_000)
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
    await page.getByRole('button', { name: '本地资料库' }).click()
    await expect(page.locator('input[type=file]')).toHaveCount(2)
    const uploaded = page.waitForResponse(r => new URL(r.url()).pathname === '/api/documents' && r.request().method() === 'POST')
    await page.locator('input[type=file]').first().setInputFiles({ name: 'lifecycle.txt', mimeType: 'text/plain',
      buffer: Buffer.from('功能验收资料：工作空间成员权限在后端校验。删除资料后，不应在知识库检索结果中出现。') })
    expect((await uploaded).status()).toBe(202)
    await expect.poll(async () => {
      const docs = await (await page.request.get('/api/documents', { headers })).json()
      const doc = docs.find((d: any) => d.name === 'lifecycle.txt')
      documentId = doc?.id || ''
      return doc?.status
    }, { timeout: 90_000 }).toBe('ready')
    const found = await (await page.request.post('/api/documents/search', { headers, data: { query: '工作空间成员权限' } })).json()
    expect(found.some((d: any) => d.document_id === documentId)).toBeTruthy()
    const created = await page.request.post('/api/research/runs', { headers, data: {
      topic: '根据本地功能验收资料，说明工作空间成员权限在哪里校验，以及删除资料后的检索预期。',
      data_policy: 'internal', mode: 'quick', client_request_id: `internal-document-${Date.now()}`,
    } })
    expect(created.status()).toBe(202)
    const run = await created.json()
    threadId = run.thread_id
    let result: any
    const deadline = Date.now() + 180_000
    while (Date.now() < deadline) {
      result = await (await page.request.get(`/api/research/runs/${run.id}`, { headers })).json()
      if (['completed', 'insufficient', 'failed', 'interrupted'].includes(result.status)) break
      await page.waitForTimeout(2000)
    }
    expect(['completed', 'insufficient']).toContain(result.status)
    expect(result.sources.every((source: any) => source.kind !== 'web')).toBeTruthy()
    if (result.status === 'completed')
      expect(result.sources.some((source: any) => source.kind === 'local' && source.document_id === documentId)).toBeTruthy()
  } finally {
    if (threadId) expect((await page.request.delete(`/api/threads/${threadId}`, { headers })).ok()).toBeTruthy()
    if (documentId) expect((await page.request.delete(`/api/documents/${documentId}`, { headers })).ok()).toBeTruthy()
  }
  const found = await (await page.request.post('/api/documents/search', { headers, data: { query: '工作空间成员权限' } })).json()
  expect(found.some((d: any) => d.document_id === documentId)).toBeFalsy()
  const reviewerName = process.env.E2E_REVIEWER_USERNAME
  const reviewerPassword = process.env.E2E_REVIEWER_PASSWORD
  const reviewerSubject = process.env.E2E_REVIEWER_SUBJECT
  if (reviewerName && reviewerPassword && reviewerSubject) {
    const workspaces = await (await page.request.get('/api/workspaces', { headers })).json()
    const workspace = workspaces[0].id
    expect((await page.request.put(`/api/workspaces/${workspace}/members`, { headers,
      data: { subject: reviewerSubject, role: 'viewer' } })).ok()).toBeTruthy()
    const viewerContext = await browser.newContext()
    try {
      const viewer = await viewerContext.newPage()
      await viewer.goto('/')
      await viewer.getByRole('button', { name: '登录并开始研究' }).click()
      await viewer.locator('#username').fill(reviewerName)
      await viewer.locator('#password').fill(reviewerPassword)
      await viewer.locator('#kc-login').click()
      await expect(viewer.getByRole('button', { name: /退出/ })).toBeVisible()
      const viewerToken = await viewer.evaluate(() => sessionStorage.getItem('dr-token'))
      expect((await viewer.request.post('/api/research/runs', {
        headers: { Authorization: `Bearer ${viewerToken}`, 'X-Workspace-ID': workspace },
        data: { topic: 'should be rejected', client_request_id: 'viewer-rejection' },
      })).status()).toBe(403)
    } finally {
      await viewerContext.close()
    }
  }
})
