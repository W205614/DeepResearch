import { expect, test } from '@playwright/test'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

test('bounded real generation: evidence, export and insufficient explanation', async ({ browser }) => {
  test.skip(process.env.E2E_IMPROVEMENT_LIVE !== '1', 'Explicit supplier budget and isolated retained accounts required')
  test.setTimeout(360_000)
  const accounts = JSON.parse(readFileSync(resolve('../.cache/lifecycle-acceptance/accounts.private.json'), 'utf8'))
  const roles = JSON.parse(readFileSync(resolve('../.cache/lifecycle-acceptance/preserved-role-result.json'), 'utf8'))
  const context = await browser.newContext()
  const page = await context.newPage()
  const results: any[] = []
  const output = resolve('../.cache/improvement-20261025/browser')
  mkdirSync(output, { recursive: true })
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '登录并开始研究' }).click()
    await page.locator('#username').fill(accounts.author.username)
    await page.locator('#password').fill(accounts.author.password)
    await page.locator('#kc-login').click()
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible({ timeout: 30_000 })
    const headers = { Authorization: `Bearer ${await page.evaluate(() => sessionStorage.getItem('dr-token'))}`, 'X-Workspace-ID': accounts.author.id }
    for (const [kind, topic] of [
      ['normal', '根据本地报销制度，正式员工报销上限是多少？实习生是否适用？请只依据资料作答。'],
      ['insufficient', '根据本地报销制度，2027年火星基地员工的报销额度是多少？如果资料没有这个事实请明确说明，禁止外推。'],
    ]) {
      await page.getByRole('button', { name: '新建研究', exact: true }).click()
      await page.getByLabel('资料范围', { exact: true }).selectOption('internal')
      await page.getByLabel('研究主题', { exact: true }).fill(topic)
      const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/runs' && response.request().method() === 'POST')
      await page.getByRole('button', { name: '开始研究', exact: true }).click()
      const initial = await (await created).json()
      let result: any
      await expect.poll(async () => {
        result = await (await page.request.get(`/api/research/runs/${initial.id}`, { headers })).json()
        return result.status
      }, { timeout: 150_000, intervals: [1000, 2000, 4000] }).toMatch(/^(completed|insufficient|failed|cancelled|interrupted)$/)
      expect(['completed', 'insufficient'], JSON.stringify({ error: result.error })).toContain(result.status)
      if (kind === 'normal') {
        expect(result.report).toContain('800')
        expect(result.report).toMatch(/实习生.*不适用/)
        expect(result.sources.some((source: any) => source.document_id === roles.docId)).toBe(true)
        await expect(page.getByRole('button', { name: '导出 Markdown', exact: true })).toBeEnabled({ timeout: 10_000 })
        const download = page.waitForEvent('download')
        await page.getByRole('button', { name: '导出 Markdown', exact: true }).click()
        await (await download).saveAs(resolve(output, 'normal-report.private.md'))
      } else {
        expect(result.report).toMatch(/不足|未知|未提供|无法|未能/)
        expect(result.validation.original_request_answered).not.toBe(true)
      }
      results.push({ kind, status: result.status, quality: result.validation.quality,
        original_request_answered: result.validation.original_request_answered, source_count: result.sources.length })
      await page.screenshot({ path: resolve(output, `${kind}.png`), fullPage: true })
      writeFileSync(resolve(output, 'summary.json'), JSON.stringify({ results, mode: 'real model, real OIDC/backend/database/browser; deterministic embedding, no external user feedback' }, null, 2))
    }
  } finally {
    await context.close()
  }
})

test('saved real report evidence and zero-cost insufficient-state browser check', async ({ browser }) => {
  test.skip(process.env.E2E_IMPROVEMENT_STATE !== '1', 'Explicit isolated fixture state check only')
  test.setTimeout(150_000)
  const accounts = JSON.parse(readFileSync(resolve('../.cache/lifecycle-acceptance/accounts.private.json'), 'utf8'))
  const context = await browser.newContext()
  const page = await context.newPage()
  const output = resolve('../.cache/improvement-20261025/browser')
  try {
    await page.goto('/')
    await page.getByRole('button', { name: '登录并开始研究' }).click()
    await page.locator('#username').fill(accounts.author.username)
    await page.locator('#password').fill(accounts.author.password)
    await page.locator('#kc-login').click()
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible({ timeout: 30_000 })
    await page.locator('.thread-select').filter({ hasText: '根据本地报销制度，正式员工报销上限是多少' }).first().click()
    await page.getByRole('button', { name: /查看证据片段/ }).first().click()
    await expect(page.getByRole('dialog')).toContainText('800 元')
    await expect(page.getByRole('dialog')).toContainText('实习生仍不适用')
    await page.screenshot({ path: resolve(output, 'saved-real-evidence.png'), fullPage: true })
    await page.getByRole('button', { name: '关闭来源' }).click()
    await page.getByRole('button', { name: '新建研究', exact: true }).click()
    await page.getByLabel('资料范围', { exact: true }).selectOption('internal')
    await page.getByLabel('研究主题', { exact: true }).fill('隔离体验夹具：来源缺少目标事实，请说明证据不足。')
    const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/runs' && response.request().method() === 'POST')
    await page.getByRole('button', { name: '开始研究', exact: true }).click()
    const run = await (await created).json()
    const headers = { Authorization: `Bearer ${await page.evaluate(() => sessionStorage.getItem('dr-token'))}`, 'X-Workspace-ID': accounts.author.id }
    let result: any
    await expect.poll(async () => {
      result = await (await page.request.get(`/api/research/runs/${run.id}`, { headers })).json()
      return result.status
    }, { timeout: 90_000, intervals: [1000, 2000] }).toBe('insufficient')
    expect(result.report).toMatch(/研究资料不足|未取得足以支持结论/)
    await expect(page.locator('.report-card')).toContainText('研究资料不足')
    await expect(page.getByRole('button', { name: '提交人工审核', exact: true })).toBeDisabled()
    await page.screenshot({ path: resolve(output, 'insufficient-fixture.png'), fullPage: true })
    writeFileSync(resolve(output, 'state-summary.json'), JSON.stringify({
      saved_real_report_evidence_opened: true, missing_evidence_status: result.status,
      submission_blocked: true, model: 'deterministic fixture for missing-evidence state; no generation quality claim',
    }, null, 2))
  } finally {
    await context.close()
  }
})
