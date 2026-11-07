import type { Page } from '@playwright/test'

export const fixtureIssuer = 'http://localhost:8180/realms/deepresearch'
export const fixtureAuthConfig = (issuer = fixtureIssuer) => ({
  mode: 'oidc', issuer, client_id: 'deepresearch-web', registration_enabled: true,
})

// UI simulations use real-shaped OIDC metadata; enterprise tests retain the live provider.
export async function simulateIdentity(page: Page, issuer = fixtureIssuer) {
  await page.route(`${issuer}/.well-known/openid-configuration`, route => route.fulfill({ json: {
    issuer,
    authorization_endpoint: `${issuer}/protocol/openid-connect/auth`,
    token_endpoint: `${issuer}/protocol/openid-connect/token`,
    end_session_endpoint: `${issuer}/protocol/openid-connect/logout`,
    code_challenge_methods_supported: ['S256'],
  } }))
}
