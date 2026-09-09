import { expect, test } from '@playwright/test'

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
