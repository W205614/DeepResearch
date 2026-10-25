import { expect, test } from '@playwright/test'

test('report renderer strips active HTML while preserving readable evidence', async ({ page }) => {
  const token = `e30.${Buffer.from(JSON.stringify({ sub: 'sanitization-reader', exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.fixture`
  await page.addInitScript(value => sessionStorage.setItem('dr-token', value), token)
  const run = {
    id: 'sanitization-run', thread_id: 'sanitization-thread', topic: 'HTML净化回归', status: 'completed',
    report: '# 可读证据\n\n正常事实保留。\n\n<script>globalThis.__reportUnsafe = true</script>\n\n<img src=x onerror="globalThis.__reportUnsafe = true">\n\n<iframe srcdoc="<script>parent.__reportUnsafe = true</script>"></iframe>\n\n<form action="https://example.invalid"><input name="secret"></form>\n\n<a href="javascript:globalThis.__reportUnsafe=true">危险链接</a>\n\n[安全来源](https://example.org/evidence)',
    sources: [], usage: {}, validation: { quality: 'partial' }, created_at: '2026-10-25T00:00:00Z',
  }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    const data: Record<string, unknown> = {
      '/api/auth/config': { mode: 'oidc' },
      '/api/workspaces': [{ id: 'sanitization-reader', name: '净化回归', role: 'viewer' }],
      '/api/threads': [{ id: run.thread_id, title: run.topic }],
      '/api/status': { mode: 'enterprise', missing: [] },
      '/api/capabilities': { reasons: [], submit_research: true },
      '/api/threads/sanitization-thread/runs': [run], '/api/research/runs/sanitization-run': run,
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data[path] ?? []) })
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
  await page.locator('.thread-select').click()
  const report = page.locator('.report-reader .markdown')
  await expect(report).toContainText('正常事实保留')
  await expect(report.locator('script,img,iframe,form,[onerror]')).toHaveCount(0)
  await expect(report.locator('a[href^="javascript:"]')).toHaveCount(0)
  await expect(report.getByRole('link', { name: '安全来源' })).toHaveAttribute('href', 'https://example.org/evidence')
  expect(await page.evaluate(() => (globalThis as any).__reportUnsafe)).toBeUndefined()
})
