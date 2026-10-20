import { expect, test } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { readingReport } from './fixtures/reading-report'

test('long reports keep navigation, wide content, citations and technical details usable', async ({ page }) => {
  const token = `e30.${Buffer.from(JSON.stringify({ sub: 'reader', preferred_username: 'reading-check', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`
  await page.addInitScript(value => sessionStorage.setItem('dr-token', value), token)
  const sources = Array.from({ length: 8 }, (_, i) => ({ id: i ? `L-bcd12${i}` : 'L-abc123', kind: 'local',
    title: `阅读验收资料 ${i + 1}`, text: '此处为引用交互验收原文，不构成课程建议。',
    locator: `样本文档 · 第 ${i + 1} 节`, access: 'fulltext', retrieved_at: '2026-10-16T00:00:00Z' }))
  const run = { id: 'long-run', thread_id: 'long-thread', topic: '怎样选择适合自己的学习方式', status: 'completed',
    report: readingReport, sources, usage: { llm_calls: 3 }, created_at: '2026-10-16T00:00:00Z',
    validation: { verification_contract:'verification-v3-claim-span', quality: 'partial', checked_claims: 2, supported_claims: 1 } }
  const publication = { id: 'long-publication', topic: run.topic, status: 'published', author_subject: 'reader',
    report_markdown: readingReport, sources, validation: run.validation, content_sha256: 'a'.repeat(64),
    submitted_at: '2026-10-16T00:00:00Z', reviewed_at: '2026-10-16T01:00:00Z', reviewed_by: 'reviewer',
    review_reason: '仅供阅读布局验收，未验证具体课程效果。' }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const data: Record<string, unknown> = {
      '/api/auth/config': { mode: 'oidc' }, '/api/workspaces': [{ id: 'reader', name: '阅读体验验收', role: 'viewer' }],
      '/api/threads': [{ id: 'long-thread', title: run.topic }], '/api/status': { mode: 'enterprise', missing: [] },
      '/api/capabilities': { reasons: [], submit_research: true }, '/api/threads/long-thread/runs': [run],
      '/api/research/runs/long-run': run, '/api/reports': [publication], '/api/reports/long-publication': publication,
    }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: close\ndata: {}\n\n' })
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data[path] ?? []) })
  })

  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/')
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
    if (width <= 760) await page.getByRole('button', { name: '打开导航' }).click()
    await page.locator('.thread-select').click()
    const reader = page.locator('.report-reader')
    await expect(reader.locator('.markdown')).toContainText('局限与下一步')
    await expect(reader.locator('#comparison-record')).toHaveText('保存一份比较记录')
    await expect(page.locator('.source-card')).toHaveCount(8)
    const directory = reader.locator('.report-contents')
    await directory.locator('summary').click()
    // Repeated subheadings must each lead to their own section.
    const notes = directory.getByRole('link', { name: '注意事项', exact: true })
    expect(await notes.nth(0).getAttribute('href')).not.toBe(await notes.nth(1).getAttribute('href'))
    await directory.getByRole('link', { name: '局限与下一步', exact: true }).click()
    const lastHeading = reader.getByRole('heading', { name: '局限与下一步', exact: true })
    await expect(lastHeading).toBeFocused()
    expect((await lastHeading.boundingBox())!.y).toBeGreaterThanOrEqual(48)
    await expect(lastHeading).toBeInViewport()
    // The directory remains available after scrolling through a long document.
    expect((await directory.boundingBox())!.y).toBeLessThan(16)
    await directory.locator('summary').click()
    await directory.getByRole('link', { name: '把两种选择放在一起', exact: true }).click()
    const screenshotDirectory = process.env.E2E_SCREENSHOT_DIR
    if (screenshotDirectory) {
      mkdirSync(screenshotDirectory, { recursive: true })
      await page.evaluate(() => document.fonts.ready)
      await page.mouse.move(0, 0)
      await page.screenshot({ path: resolve(screenshotDirectory, `long-reading-${width}.png`), animations: 'disabled' })
      await directory.locator('summary').click()
      await page.screenshot({ path: resolve(screenshotDirectory, `long-contents-${width}.png`), animations: 'disabled' })
      await directory.locator('summary').click()
    }
    const table = reader.getByRole('region', { name: '报告表格，可横向滚动' })
    await table.focus()
    const dimensions = await table.evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth }))
    expect(dimensions.scroll).toBeGreaterThan(dimensions.width)
    await expect(table).toHaveClass(/is-wide/)
    await page.keyboard.press('ArrowRight')
    await expect.poll(() => table.evaluate(el => el.scrollLeft)).toBeGreaterThan(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    await reader.locator('pre').focus()
    await page.keyboard.press('ArrowRight')
    await expect.poll(() => reader.locator('pre').evaluate(el => el.scrollLeft)).toBeGreaterThan(0)
    await page.getByRole('button', { name: '核验与用量', exact: true }).click()
    await expect(page.locator('.report-metrics')).toHaveAttribute('open', '')
    await expect(page.locator('.report-metrics summary')).toBeFocused()
    await expect(page.locator('.report-metrics')).toContainText('模型调用 3 次')
    await reader.locator('.markdown p').filter({ hasText: '[L-abc123]' }).first().click()
    await expect(page.locator('.source-excerpt')).toContainText(sources[0].text)
    await page.getByRole('button', { name: '关闭来源' }).click()

    await page.evaluate(() => window.scrollTo(0, 0))
    const directoryPath = process.env.E2E_SCREENSHOT_DIR
    if (directoryPath) {
      mkdirSync(directoryPath, { recursive: true })
      await page.evaluate(() => document.fonts.ready)
      await page.mouse.move(0, 0)
      await page.screenshot({ path: resolve(directoryPath, `long-report-${width}.png`), animations: 'disabled' })
    }
    if (width <= 760) await page.getByRole('button', { name: '打开导航' }).click()
    await page.getByRole('button', { name: '正式报告', exact: true }).click()
    await page.locator('.report-list-item').click()
    await expect(page.locator('.publication-detail .report-contents')).toBeVisible()
    await page.getByRole('button', { name: '版本与审核', exact: true }).click()
    await expect(page.locator('.publication-meta')).toHaveAttribute('open', '')
    await expect(page.locator('.publication-meta code')).toHaveText('a'.repeat(64))
    await expect(page.getByRole('button', { name: '撤回正式版本' })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  }
})
