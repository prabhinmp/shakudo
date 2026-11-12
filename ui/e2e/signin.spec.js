import { test, expect } from '@playwright/test'
import { makeToken, mockApi } from './helpers.js'

test.describe('sign-in', () => {
  test('shows the sign-in screen when there is no token', async ({ page }) => {
    await mockApi(page)
    await page.goto('/ui/')
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
    await expect(page.getByTestId('deployment-card')).toHaveCount(0)
  })

  test('rejects garbage and expired tokens without calling the API', async ({ page }) => {
    const api = await mockApi(page)
    await page.goto('/ui/')
    await page.getByLabel('API token').fill('not-a-token')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('valid API token')
    await page.getByLabel('API token').fill(makeToken('admin', { expInSeconds: -60 }))
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('expired')
    expect(api.calls).toHaveLength(0)
  })

  test('valid token signs in, shows who/role, and sends it as a bearer header', async ({ page }) => {
    const api = await mockApi(page)
    const token = makeToken('operator')
    await page.goto('/ui/')
    await page.getByLabel('API token').fill(token)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByTestId('who')).toContainText('alice')
    await expect(page.getByTestId('who')).toContainText('operator')
    await expect(page.getByTestId('deployment-card')).toHaveCount(4)
    expect(api.calls[0].auth).toBe(`Bearer ${token}`)
  })

  test('sign out returns to the sign-in screen and forgets the token', async ({ page }) => {
    await mockApi(page)
    await page.addInitScript((t) => { if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); sessionStorage.setItem('deploy-ui.token', t) } }, makeToken('admin'))
    await page.goto('/ui/')
    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  })
})
