import { test, expect } from '@playwright/test'
import { ID, mockApi, signedIn } from './helpers.js'

const open = (page, n = 2) => page.goto(`/ui/#/d/${ID(n)}`)

test.describe('scale', () => {
  test('sends PATCH with the replica count and confirms', async ({ page }) => {
    await signedIn(page, 'operator')
    const api = await mockApi(page)
    await open(page)
    await page.getByLabel('Replicas', { exact: true }).fill('5')
    await page.getByRole('button', { name: 'Scale' }).click()
    await expect(page.getByTestId('toast')).toContainText('Scaled to 5 replicas')
    const patch = api.calls.find((c) => c.method === 'PATCH')
    expect(patch.path).toBe(`/api/deployments/${ID(2)}`)
    expect(patch.body).toEqual({ replicas: 5 })
    await expect(page.getByTestId('replicas-desired')).toHaveText('5') // detail refreshed straight away
  })

  test('can scale to zero', async ({ page }) => {
    await signedIn(page, 'operator')
    const api = await mockApi(page)
    await open(page)
    await page.getByLabel('Replicas', { exact: true }).fill('0')
    await page.getByRole('button', { name: 'Scale' }).click()
    await expect(page.getByTestId('toast')).toContainText('Scaled to 0')
    expect(api.calls.find((c) => c.method === 'PATCH').body).toEqual({ replicas: 0 })
  })

  for (const bad of ['', '-1', '21', '2.5']) {
    test(`rejects "${bad}" without calling the API`, async ({ page }) => {
      await signedIn(page, 'operator')
      const api = await mockApi(page)
      await open(page)
      await page.getByLabel('Replicas', { exact: true }).fill(bad)
      await page.getByRole('button', { name: 'Scale' }).click()
      await expect(page.getByTestId('scale-error')).toBeVisible()
      expect(api.count('PATCH', /deployments/)).toBe(0)
    })
  }

  test('shows the API error message when scaling fails', async ({ page }) => {
    await signedIn(page, 'operator')
    const api = await mockApi(page)
    await open(page)
    api.failNext = { method: 'PATCH', status: 400, message: 'replicas may not exceed 20' }
    await page.getByLabel('Replicas', { exact: true }).fill('20')
    await page.getByRole('button', { name: 'Scale' }).click()
    await expect(page.getByTestId('toast')).toContainText('replicas may not exceed 20')
  })
})

test('restart sends POST /restart', async ({ page }) => {
  await signedIn(page, 'operator')
  const api = await mockApi(page)
  await open(page)
  await page.getByRole('button', { name: 'Restart' }).click()
  await expect(page.getByTestId('toast')).toContainText('restart')
  expect(api.count('POST', new RegExp(`/api/deployments/${ID(2)}/restart$`))).toBe(1)
})

test.describe('delete', () => {
  test('asks for confirmation; Cancel sends nothing', async ({ page }) => {
    await signedIn(page, 'admin')
    const api = await mockApi(page)
    await open(page)
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(page.getByTestId('confirm-delete')).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByTestId('confirm-delete')).toHaveCount(0)
    expect(api.count('DELETE', /deployments/)).toBe(0)
  })

  test('the delete button stays disabled until the exact name is typed', async ({ page }) => {
    await signedIn(page, 'admin')
    await mockApi(page)
    await open(page)
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    const confirm = page.getByRole('button', { name: 'Delete permanently' })
    await expect(confirm).toBeDisabled()
    await page.getByLabel(/Type/).fill('ap')
    await expect(confirm).toBeDisabled()
    await page.getByLabel(/Type/).fill('api')
    await expect(confirm).toBeEnabled()
  })

  test('confirming deletes, closes the panel and removes the card', async ({ page }) => {
    await signedIn(page, 'admin')
    const api = await mockApi(page)
    await open(page)
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await page.getByLabel(/Type/).fill('api')
    await page.getByRole('button', { name: 'Delete permanently' }).click()
    await expect(page.getByTestId('toast')).toContainText('deleted')
    await expect(page.getByTestId('detail')).toHaveCount(0)
    expect(api.count('DELETE', new RegExp(`/api/deployments/${ID(2)}$`))).toBe(1)
    await expect(page.getByTestId('deployment-card')).toHaveCount(3)
    await expect(page.getByTestId('deployment-card').filter({ hasText: /^api/ })).toHaveCount(0)
  })

  test('Escape closes the confirmation', async ({ page }) => {
    await signedIn(page, 'admin')
    const api = await mockApi(page)
    await open(page)
    await page.getByRole('button', { name: 'Delete', exact: true }).click()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('confirm-delete')).toHaveCount(0)
    expect(api.count('DELETE', /deployments/)).toBe(0)
  })
})

test.describe('role gating (UI hints; the API still enforces)', () => {
  const state = async (page) => ({
    scale: await page.getByRole('button', { name: 'Scale' }).isEnabled(),
    restart: await page.getByRole('button', { name: 'Restart' }).isEnabled(),
    del: await page.getByRole('button', { name: 'Delete', exact: true }).isEnabled(),
  })
  for (const [role, expected] of [
    ['viewer', { scale: false, restart: false, del: false }],
    ['operator', { scale: true, restart: true, del: false }],
    ['admin', { scale: true, restart: true, del: true }],
  ]) {
    test(`${role}`, async ({ page }) => {
      await signedIn(page, role)
      await mockApi(page)
      await open(page)
      await expect(page.getByTestId('detail-name')).toHaveText('api')
      expect(await state(page)).toEqual(expected)
    })
  }

  test('a server-side 403 is still shown if a viewer somehow gets through', async ({ page }) => {
    await signedIn(page, 'operator')
    const api = await mockApi(page)
    await open(page)
    api.failNext = { method: 'PATCH', status: 403, message: "Role 'operator' may not perform this action" }
    await page.getByLabel('Replicas', { exact: true }).fill('2')
    await page.getByRole('button', { name: 'Scale' }).click()
    await expect(page.getByTestId('toast')).toContainText('may not perform')
  })
})

test('API text is rendered as text, never as HTML (XSS guard)', async ({ page }) => {
  await signedIn(page, 'viewer')
  const api = await mockApi(page)
  api.items[0].name = '<img src=x onerror="window.__pwned=1">'
  await page.goto('/ui/')
  await expect(page.getByTestId('deployment-card').first()).toContainText('<img src=x')
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined()
})
