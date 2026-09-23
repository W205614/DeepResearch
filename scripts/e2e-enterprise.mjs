import { randomBytes } from 'node:crypto'
import { execFile } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execute = promisify(execFile)
const keycloakBase = (process.env.E2E_KEYCLOAK_BASE_URL || 'http://127.0.0.1:8180').replace(/\/$/, '')
const realm = 'deepresearch'
const username = `e2e-${Date.now()}-${randomBytes(3).toString('hex')}`
const password = randomBytes(24).toString('base64url')
const reviewerUsername = `e2e-reviewer-${Date.now()}-${randomBytes(3).toString('hex')}`
const reviewerPassword = randomBytes(24).toString('base64url')
let userId = ''
let reviewerId = ''

function readDotenvValue(name) {
  try {
    const line = readFileSync(new URL('../.env', import.meta.url), 'utf8')
      .split(/\r?\n/).find((item) => item.startsWith(`${name}=`))
    return line?.slice(name.length + 1).trim() || ''
  } catch {
    return ''
  }
}

async function responseJson(response, label) {
  if (!response.ok) throw new Error(`${label}: ${response.status} ${await response.text()}`)
  return response.json()
}

async function adminToken(adminPassword) {
  const body = new URLSearchParams({
    client_id: 'admin-cli', grant_type: 'password', username: 'admin', password: adminPassword,
  })
  const response = await fetch(`${keycloakBase}/realms/master/protocol/openid-connect/token`, {
    method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' }, body,
  })
  return (await responseJson(response, 'Keycloak admin login')).access_token
}

async function createUser(token, name, secret) {
  const response = await fetch(`${keycloakBase}/admin/realms/${realm}/users`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      username: name, enabled: true, emailVerified: true,
      email: `${name}@example.invalid`, firstName: 'E2E', lastName: 'Verification',
      credentials: [{ type: 'password', value: secret, temporary: false }],
    }),
  })
  if (!response.ok) throw new Error(`Create temporary E2E user: ${response.status} ${await response.text()}`)
  const location = response.headers.get('location') || ''
  const id = location.split('/').at(-1) || ''
  if (!id) throw new Error('Keycloak did not return the temporary user identifier')
  return id
}

async function cleanupApplicationData() {
  if (!userId && !reviewerId) return
  const ids = [userId, reviewerId].filter(Boolean)
  const quoted = ids.map(id => `'${id}'`).join(',')
  const sql = [
    `DELETE FROM report_publications WHERE workspace_id IN (${quoted})`,
    `DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE user_id IN (${quoted}))`,
    `DELETE FROM business_outbox WHERE aggregate_id IN (SELECT id FROM runs WHERE user_id IN (${quoted}))`,
    `DELETE FROM dead_letter_runs WHERE user_id IN (${quoted})`,
    `DELETE FROM counters WHERE run_id IN (SELECT id FROM runs WHERE user_id IN (${quoted}))`,
    `DELETE FROM search_cache WHERE run_id IN (SELECT id FROM runs WHERE user_id IN (${quoted}))`,
    `DELETE FROM runs WHERE user_id IN (${quoted})`,
    `DELETE FROM threads WHERE user_id IN (${quoted})`,
    `DELETE FROM memories WHERE user_id IN (${quoted}) OR owner_subject IN (${quoted})`,
    `DELETE FROM document_search_cache WHERE user_id IN (${quoted})`,
    `DELETE FROM chunks WHERE user_id IN (${quoted})`,
    `DELETE FROM documents WHERE user_id IN (${quoted})`,
    `DELETE FROM workspace_daily_usage WHERE workspace_id IN (${quoted})`,
    `DELETE FROM audit_logs WHERE workspace_id IN (${quoted})`,
    `DELETE FROM workspace_limits WHERE workspace_id IN (${quoted})`,
    `DELETE FROM memberships WHERE workspace_id IN (${quoted}) OR subject IN (${quoted})`,
    `DELETE FROM workspaces WHERE id IN (${quoted})`,
  ].join('; ')
  await execute('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'deepresearch', '-d', 'deepresearch', '-c', sql])
}

async function deleteUser(token, id) {
  if (id) {
    const response = await fetch(`${keycloakBase}/admin/realms/${realm}/users/${id}`, {
      method: 'DELETE', headers: { authorization: `Bearer ${token}` },
    })
    if (!response.ok && response.status !== 404) throw new Error(`Delete temporary E2E user: ${response.status}`)
  }
}

const adminPassword = process.env.E2E_KEYCLOAK_ADMIN_PASSWORD || process.env.KEYCLOAK_ADMIN_PASSWORD || readDotenvValue('KEYCLOAK_ADMIN_PASSWORD')
if (!adminPassword) throw new Error('Set E2E_KEYCLOAK_ADMIN_PASSWORD or KEYCLOAK_ADMIN_PASSWORD; it is never written to test reports.')

let token = ''
let exitCode = 1
try {
  token = await adminToken(adminPassword)
  userId = await createUser(token, username, password)
  reviewerId = await createUser(token, reviewerUsername, reviewerPassword)
  const frontendDir = fileURLToPath(new URL('../frontend/', import.meta.url))
  const playwrightCli = fileURLToPath(new URL('../frontend/node_modules/@playwright/test/cli.js', import.meta.url))
  const result = await execute(process.execPath, [playwrightCli, 'test', '--workers=1',
    ...(process.env.E2E_GREP ? ['--grep', process.env.E2E_GREP] : [])], {
    cwd: frontendDir,
    env: { ...process.env, E2E_USERNAME: username, E2E_PASSWORD: password,
      E2E_REVIEWER_USERNAME: reviewerUsername, E2E_REVIEWER_PASSWORD: reviewerPassword,
      E2E_AUTHOR_SUBJECT: userId, E2E_REVIEWER_SUBJECT: reviewerId },
  })
  process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  exitCode = 0
} catch (error) {
  if (error.stdout) process.stdout.write(error.stdout)
  if (error.stderr) process.stderr.write(error.stderr)
  process.stderr.write('Enterprise browser acceptance failed; inspect local test-results.\n')
} finally {
  try { await cleanupApplicationData() } finally {
    const cleanupToken = await adminToken(adminPassword)
    await deleteUser(cleanupToken, userId)
    await deleteUser(cleanupToken, reviewerId)
  }
}
process.exitCode = exitCode
