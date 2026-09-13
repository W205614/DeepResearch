import { expect, test } from '@playwright/test'

test('data scope is explicit and failed partial results cannot masquerade as complete', async ({ page }) => {
  const issuer = 'http://localhost:8180/realms/deepresearch'
  await page.addInitScript(({ issuer }) => {
    const token = btoa('{}') + '.' + btoa(JSON.stringify({ sub: 'fixture', preferred_username: 'fixture', iss: issuer, exp: Math.floor(Date.now()/1000)+3600 })) + '.fixture'
    sessionStorage.setItem('dr-token', token)
    localStorage.removeItem('dr-thread')
  }, { issuer })
  let policy = ''
  const run = { id: 'fixture-run', thread_id: 'fixture-thread', topic: '验证故障边界', status: 'failed',
    report: '# 已核验的部分结果\n\n其余内容尚未完成核验。', sources: [], usage: {}, created_at: new Date().toISOString(),
    error: '累计预算耗尽', can_resume: false, error_info: { code: 'budget_exhausted', retryable: false, action: '缩小问题后创建新任务' },
    validation: { quality: 'partial', verification_pending: true } }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: 'event: close\ndata: {}\n\n' })
    let body: unknown = {}
    if (path === '/api/auth/config') body = { mode: 'oidc', issuer }
    else if (path === '/api/workspaces') body = [{ id: 'fixture', name: '试用工作空间', role: 'admin' }]
    else if (path === '/api/threads') body = []
    else if (path === '/api/status') body = { configured: true }
    else if (path === '/api/capabilities') body = { read_history: true, submit_research: true, reasons: ['vector_unavailable'] }
    else if (path === '/api/research/runs' && route.request().method() === 'POST') {
      policy = route.request().postDataJSON().data_policy
      body = run
    } else if (path === '/api/research/runs/fixture-run') body = run
    await route.fulfill({ status: path === '/api/research/runs' ? 202 : 200, json: body })
  })
  await page.goto('/')
  await expect(page.getByLabel('资料范围')).toHaveValue('internal')
  await expect(page.getByText(/部分服务暂不可用/)).toBeVisible()
  await page.getByLabel('资料范围').selectOption('public')
  await page.getByLabel('研究主题').fill('验证故障边界')
  await page.getByRole('button', { name: '开始研究', exact: true }).click()
  await expect(page.getByText('部分结果：仍有未回答或未核验内容')).toBeVisible()
  await expect(page.getByText('缩小问题后创建新任务')).toBeVisible()
  await expect(page.getByRole('button', { name: '从检查点继续' })).toHaveCount(0)
  expect(policy).toBe('public')
  await page.screenshot({ path: '../.cache/reliability-ui.png', fullPage: true })
})
