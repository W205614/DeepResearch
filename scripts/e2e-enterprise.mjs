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
let userId = ''

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

async function createUser(token) {
  const response = await fetch(`${keycloakBase}/admin/realms/${realm}/users`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      username, enabled: true, emailVerified: true,
      email: `${username}@example.invalid`, firstName: 'E2E', lastName: 'Verification',
      credentials: [{ type: 'password', value: password, temporary: false }],
    }),
  })
  if (!response.ok) throw new Error(`Create temporary E2E user: ${response.status} ${await response.text()}`)
  const location = response.headers.get('location') || ''
  userId = location.split('/').at(-1) || ''
  if (!userId) throw new Error('Keycloak did not return the temporary user identifier')
}

async function cleanupApplicationData() {
  if (!userId) return
  const sql = [
    `DELETE FROM audit_logs WHERE workspace_id = '${userId}'`,
    `DELETE FROM workspace_limits WHERE workspace_id = '${userId}'`,
    `DELETE FROM memberships WHERE workspace_id = '${userId}' OR subject = '${userId}'`,
    `DELETE FROM workspaces WHERE id = '${userId}'`,
  ].join('; ')
  await execute('docker', ['compose', 'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'deepresearch', '-d', 'deepresearch', '-c', sql])
}

async function deleteUser(token) {
  if (userId) {
    const response = await fetch(`${keycloakBase}/admin/realms/${realm}/users/${userId}`, {
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
  await createUser(token)
  const frontendDir = fileURLToPath(new URL('../frontend/', import.meta.url))
  const playwrightCli = fileURLToPath(new URL('../frontend/node_modules/@playwright/test/cli.js', import.meta.url))
  const result = await execute(process.execPath, [playwrightCli, 'test'], {
    cwd: frontendDir,
    env: { ...process.env, E2E_USERNAME: username, E2E_PASSWORD: password },
  })
  process.stdout.write(result.stdout)
  if (result.stderr) process.stderr.write(result.stderr)
  exitCode = 0
} finally {
  try { await cleanupApplicationData() } finally { await deleteUser(token) }
}
process.exitCode = exitCode