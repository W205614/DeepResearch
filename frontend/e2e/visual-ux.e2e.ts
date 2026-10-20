import { expect, test, type Page } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const sizes = [{ width: 1440, height: 1000 }, { width: 768, height: 1024 }, { width: 390, height: 844 }]

async function fitsViewport(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
}

async function capture(page: Page, name: string) {
  const directory = process.env.E2E_SCREENSHOT_DIR
  if (!directory) return
  mkdirSync(directory, { recursive: true })
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.mouse.move(0, 0)
  await page.screenshot({ path: resolve(directory, `${name}.png`), fullPage: true, animations: 'disabled' })
}

async function navigate(page: Page, name: string) {
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
  const menu = page.getByRole('button', { name: '打开导航', exact: true })
  if (await menu.isVisible()) await menu.click()
  await page.getByRole('button', { name, exact: true }).click()
}

test('sign-in stays readable, keyboard accessible and usable across viewport sizes', async ({ page }) => {
  for (const size of sizes) {
    await page.setViewportSize(size)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '让研究有据可循。' })).toBeVisible()
    const login = page.getByRole('button', { name: '登录并开始研究', exact: true })
    await expect(login).toBeVisible()
    await page.keyboard.press('Tab')
    await expect(login).toBeFocused()
    expect((await login.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    await fitsViewport(page)
    await capture(page, `login-${size.width}`)
  }
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await page.locator('.auth-statement').evaluate(element => getComputedStyle(element).animationName)).toBe('none')
})

test('report reading, source drawer and follow-up controls fit narrow screens', async ({ page }) => {
  // This isolated fixture checks rendering of a partial result; the enterprise suite covers real authorization.
  const token = `e30.${Buffer.from(JSON.stringify({ sub: 'visual-reader', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`
  await page.addInitScript(value => sessionStorage.setItem('dr-token', value), token)
  const source = { id: 'L-abc123', kind: 'local', title: '企业知识库选型记录', url: '',
    text: 'RAG 的结论取决于检索资料的覆盖范围，长上下文也需要核对来源。', access: 'fulltext',
    locator: '选型记录.md · 字符 20–56', retrieved_at: '2026-10-16T00:00:00Z', published_at: '' }
  const run = { id: 'visual-run', thread_id: 'visual-thread', topic: '比较 RAG 与长上下文的适用范围',
    status: 'completed', sources: [source], created_at: '2026-10-16T00:00:00Z', error: '', usage: {},
    report: '# 资料范围与选型依据\n\n两种方案都需要核对来源 [L-abc123]。\n\n| 方案 | 核对事项 |\n| --- | --- |\n| RAG | 检索覆盖范围 |\n| 长上下文 | 输入资料的完整性 |',
    validation: { verification_contract:'verification-v3-claim-span', quality: 'partial', checked_claims: 1, supported_claims: 1 } }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const data: Record<string, unknown> = {
      '/api/auth/config': { mode: 'oidc' }, '/api/workspaces': [{ id: 'visual-reader', name: '视觉验收', role: 'admin' }],
      '/api/threads': [{ id: 'visual-thread', thread_key: 'thread01', title: run.topic }],
      '/api/status': { mode: 'enterprise', missing: [] }, '/api/capabilities': { reasons: [], submit_research: true },
      '/api/threads/visual-thread/runs': [run], '/api/research/runs/visual-run': run,
    }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: close\ndata: {}\n\n' })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data[path] ?? []) })
  })
  for (const size of sizes) {
    await page.setViewportSize(size)
    await page.goto('/')
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
    if (size.width <= 760) await page.getByRole('button', { name: '打开导航' }).click()
    await page.locator('.thread-select').click()
    await expect(page.getByText('部分结果：仍有未回答或未核验内容', { exact: true })).toBeVisible()
    await expect(page.locator('.report-card .markdown')).toContainText('两种方案都需要核对来源')
    await expect(page.locator('.report-contents')).toHaveCount(0)
    await expect(page.locator('.node-track')).toHaveCount(0)
    const bodyBeforeActions = await page.evaluate(() => {
      const report = document.querySelector('.report-card .markdown')!
      const actions = document.querySelector('.report-actions')!
      return Boolean(report.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING)
    })
    expect(bodyBeforeActions).toBeTruthy()
    expect((await page.locator('.report-card .markdown').boundingBox())!.y).toBeLessThan(size.height)
    await page.getByRole('button', { name: '展开研究过程', exact: true }).click()
    await expect(page.locator('.node-track')).toContainText('协调路由 Agent')
    await page.getByRole('button', { name: '收起研究过程', exact: true }).click()
    await expect(page.locator('.node-track')).toHaveCount(0)
    await fitsViewport(page)
    await page.locator('.source-card').click()
    await expect(page.locator('.source-excerpt')).toContainText(source.text)
    await expect(page.getByRole('dialog', { name: source.title, exact: true })).toBeVisible()
    const closeSource = page.getByRole('button', { name: '关闭来源' })
    await expect(closeSource).toBeFocused()
    await expect(page.locator('main')).toHaveAttribute('inert', '')
    await page.keyboard.press('Shift+Tab')
    await expect(page.locator('.source-technical summary')).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(closeSource).toBeFocused()
    await expect(page.locator('.source-technical')).not.toHaveAttribute('open', '')
    await page.getByText('来源技术信息（标识、系统分类）', { exact: true }).click()
    await expect(page.locator('.source-technical')).toContainText(source.id)
    await fitsViewport(page)
    await page.keyboard.press('Escape')
    await expect(page.locator('.source-drawer')).toHaveCount(0)
    await expect(page.locator('.source-card')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(closeSource).toBeFocused()
    await closeSource.click()
    await expect(page.locator('.source-drawer')).toHaveCount(0)
    await expect(page.locator('main')).not.toHaveAttribute('inert', '')
    await page.getByLabel('继续追问').fill('请进一步限定资料年份')
    await expect(page.getByRole('button', { name: '发送追问', exact: true })).toBeEnabled()
  }
})

test('real account can use prompts, scope, memory and all navigation at three sizes', async ({ page }) => {
  test.skip(!process.env.E2E_USERNAME || !process.env.E2E_PASSWORD, 'Enterprise runner provides a temporary OIDC account.')
  test.setTimeout(120_000)
  await page.goto('/')
  await page.getByRole('button', { name: '登录并开始研究' }).click()
  await page.locator('#username').fill(process.env.E2E_USERNAME!)
  await page.locator('#password').fill(process.env.E2E_PASSWORD!)
  await page.locator('#kc-login').click()
  await expect(page.getByLabel('研究主题')).toBeVisible()
  let submissions = 0
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/research/runs' && request.method() === 'POST') submissions++ })
  for (const size of sizes) {
    await page.setViewportSize(size)
    await navigate(page, '新建研究')
    await page.getByLabel('资料范围').selectOption('internal')
    await expect(page.locator('#scope-help')).toContainText('不联网')
    await page.getByRole('button', { name: /比较两种选择/ }).click()
    await expect(page.getByLabel('研究主题')).toHaveValue(/线上课程和线下课程/)
    await expect(page.getByLabel('资料范围')).toHaveValue('internal')
    if (size.width === 390) {
      await page.evaluate(() => window.scrollTo(0, 0))
      await expect(page.getByRole('button', { name: '开始研究', exact: true })).toBeInViewport()
      await expect(page.getByRole('button', { name: /比较两种选择/ })).toBeInViewport()
    }
    await expect(page.getByText('点击填入问题，资料范围保持当前选择', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: /了解一个话题/ }).click()
    await expect(page.getByLabel('研究主题')).toHaveValue(/远程办公/)
    await page.getByLabel('资料范围').selectOption('public')
    await expect(page.locator('#scope-help')).toContainText('不读取本地资料、私有历史与个人记忆')
    await expect(page.locator('#scope-help')).toContainText('个人记忆')
    await page.getByLabel('研究主题').press('Shift+Enter')
    expect(submissions).toBe(0)
    await page.getByLabel('研究主题').fill('')
    await expect(page.getByRole('button', { name: '开始研究', exact: true })).toBeDisabled()
    await fitsViewport(page)
    await capture(page, `workspace-${size.width}`)
    await page.getByRole('button', { name: /读懂自己的资料/ }).click()
    await expect(page.getByRole('button', { name: '选择本地资料' })).toBeVisible()
    await fitsViewport(page)
    await capture(page, `documents-${size.width}`)
    await navigate(page, '正式报告')
    await expect(page.getByRole('heading', { name: '正式报告', exact: true })).toBeVisible()
    await fitsViewport(page)
    await navigate(page, '研究记忆')
    await expect(page.getByLabel('添加一条研究偏好')).toBeVisible()
    await fitsViewport(page)
    await capture(page, `memory-${size.width}`)
    await navigate(page, '工作台设置')
    await expect(page.getByRole('heading', { name: '你的研究工作空间。' })).toBeVisible()
    await fitsViewport(page)
  }
  await navigate(page, '研究记忆')
  const preference = '界面验收：报告明确资料年份。'
  await page.getByLabel('添加一条研究偏好').fill(preference)
  await page.getByRole('button', { name: '保存偏好', exact: true }).click()
  await expect(page.locator('.memory-card').filter({ hasText: preference })).toBeVisible()
  await page.reload()
  await navigate(page, '研究记忆')
  const saved = page.locator('.memory-card').filter({ hasText: preference })
  await expect(saved).toBeVisible()
  await saved.getByRole('button', { name: '删除记忆' }).click()
  await expect(saved).toHaveCount(0)
})
