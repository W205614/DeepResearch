import { expect, test, type BrowserContext, type Page } from '@playwright/test'

test('two accounts submit, approve and withdraw a frozen report in the browser', async ({ browser }) => {
  const state = { status: '' as ''|'pending'|'published'|'withdrawn', reviewedBy: '', withdrawnAt: '' }
  let quality = 'partial'
  const evidence = { id: 'W-123', kind: 'web', title: 'Research source', url: 'https://example.invalid/source',
    text: 'Quoted evidence', access: 'fulltext', locator: 'section 2', published_at: '',
    retrieved_at: '2026-09-23T00:00:00Z', original_available: null }
  const publication = () => ({ id: 'publication-1', workspace_id: 'workspace-1', source_run_id: 'run-1',
    topic: 'Review test report', author_subject: 'author', report_markdown: '# Original report\n\nClaim [W-123]',
    sources: [evidence], validation: { quality: 'complete', checked_claims: 1, supported_claims: 1 },
    content_sha256: 'a'.repeat(64), status: state.status, submitted_at: '2026-09-23T00:00:00Z',
    reviewed_by: state.reviewedBy, reviewed_at: state.reviewedBy ? '2026-09-23T01:00:00Z' : '',
    review_reason: '', withdrawn_by: state.withdrawnAt ? 'reviewer' : '',
    withdrawn_at: state.withdrawnAt, withdrawal_reason: state.withdrawnAt ? 'Source updated' : '' })
  const run = () => ({ id: 'run-1', thread_id: 'thread-1', created_by: 'author', topic: 'Review test report',
    status: 'completed', report: '# Original report\n\nClaim [W-123]', sources: [evidence],
    error: '', created_at: '2026-09-23T00:00:00Z', validation: { quality, checked_claims: 1,
      supported_claims: 1 }, usage: {}, publication: state.status ? { id: 'publication-1', status: state.status } : null })

  async function account(subject: string, role: string): Promise<{ context: BrowserContext; page: Page }> {
    const context = await browser.newContext()
    const token = `${Buffer.from('{}').toString('base64url')}.${Buffer.from(JSON.stringify({
      sub: subject, preferred_username: subject, iss: 'http://localhost:8180/realms/deepresearch',
      exp: Math.floor(Date.now() / 1000) + 3600,
    })).toString('base64url')}.signature`
    await context.addInitScript(value => sessionStorage.setItem('dr-token', value), token)
    const page = await context.newPage()
    await page.route('**/api/**', async route => {
      const request = route.request()
      const url = new URL(request.url())
      const path = url.pathname
      const json = async (body: unknown, status = 200) => route.fulfill({ status,
        contentType: 'application/json', body: JSON.stringify(body) })
      if (path === '/api/auth/config') return json({ mode: 'oidc', issuer: 'http://localhost:8180/realms/deepresearch' })
      if (path === '/api/workspaces') return json(subject === 'reviewer'
        ? [{ id: 'reviewer-home', name: 'Personal', role: 'admin' }, { id: 'workspace-1', name: 'Review', role }]
        : [{ id: 'workspace-1', name: 'Review', role }])
      if (path === '/api/threads') return json(subject === 'author' ? [{ id: 'thread-1', thread_key: 'thread01', title: 'Review test thread' }] : [])
      if (path === '/api/status') return json({ mode: 'enterprise', missing: [] })
      if (path === '/api/capabilities') return json({ reasons: [], submit_research: true })
      if (path === '/api/threads/thread-1/runs') return json([run()])
      if (path === '/api/research/runs/run-1/events') return route.fulfill({
        status: 200, contentType: 'text/event-stream', body: 'event: close\ndata: {}\n\n' })
      if (path === '/api/research/runs/run-1') return json(run())
      if (path === '/api/research/runs/run-1/publication' && request.method() === 'POST') {
        state.status = 'pending'; return json(publication(), 201)
      }
      if (path.startsWith('/api/reports') && request.headers()['x-workspace-id'] !== 'workspace-1')
        return json([], 403)
      if (path === '/api/reports') return json(state.status === 'published' ? [publication()] : [])
      if (path === '/api/reports/pending') return json(state.status === 'pending' ? [publication()] : [])
      if (path === '/api/reports/archived') return json(state.status === 'withdrawn' ? [publication()] : [])
      if (path === '/api/reports/publication-1/approve') {
        state.status = 'published'; state.reviewedBy = subject; return json(publication())
      }
      if (path === '/api/reports/publication-1/withdraw') {
        state.status = 'withdrawn'; state.withdrawnAt = '2026-09-23T02:00:00Z'; return json(publication())
      }
      if (path === '/api/reports/publication-1') return json(publication())
      return json({ detail: `Unhandled test endpoint ${path}` }, 404)
    })
    await page.goto('/')
    await expect(page.getByRole('button', { name: /退出/ })).toBeVisible()
    return { context, page }
  }

  const author = await account('author', 'researcher')
  const reviewer = await account('reviewer', 'admin')
  try {
    await reviewer.page.getByLabel('切换工作空间').selectOption('workspace-1')
    await author.page.locator('.thread-select').filter({ hasText: 'Review test thread' }).click()
    await expect(author.page.getByRole('button', { name: '提交人工审核' })).toBeDisabled()
    await expect(author.page.getByText(/未达到送审门槛/)).toBeVisible()
    quality = 'complete'
    await author.page.reload()
    await author.page.locator('.thread-select').filter({ hasText: 'Review test thread' }).click()
    await expect(author.page.getByRole('button', { name: '提交人工审核' })).toBeVisible()
    await expect(author.page.getByRole('button', { name: '提交人工审核' })).toBeEnabled()
    await author.page.getByRole('button', { name: '提交人工审核' }).click()
    await expect(author.page.getByText('待审核', { exact: true })).toBeVisible()

    await reviewer.page.getByRole('button', { name: '正式报告', exact: true }).click()
    await reviewer.page.getByRole('button', { name: /Review test report/ }).click()
    await expect(reviewer.page.getByRole('button', { name: '批准发布' })).toBeVisible()
    await reviewer.page.getByRole('button', { name: '批准发布' }).click()
    await expect(reviewer.page.getByText('正式版本', { exact: true })).toBeVisible()

    await author.page.getByRole('button', { name: '正式报告', exact: true }).click()
    await expect(author.page.getByRole('button', { name: /Review test report/ })).toBeVisible()
    await reviewer.page.getByRole('textbox', { name: '撤回原因' }).fill('Source updated')
    await reviewer.page.getByRole('button', { name: '撤回正式版本' }).click()
    await expect(reviewer.page.getByText('撤回原因：Source updated')).toBeVisible()
    await author.page.getByRole('button', { name: '正式报告', exact: true }).click()
    await expect(author.page.getByText('暂无已发布报告。')).toBeVisible()
  } finally {
    await author.context.close()
    await reviewer.context.close()
  }
})
