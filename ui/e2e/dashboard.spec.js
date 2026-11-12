import { test, expect } from '@playwright/test'
import { dep, mockApi, signedIn } from './helpers.js'

test.beforeEach(async ({ page }) => { await signedIn(page, 'viewer') })

test('lists deployments from all namespaces with colour-coded health', async ({ page }) => {
  await mockApi(page)
  await page.goto('/ui/')
  const cards = page.getByTestId('deployment-card')
  await expect(cards).toHaveCount(4)
  await expect(cards.filter({ hasText: 'web' })).toHaveAttribute('data-health', 'healthy')
  await expect(cards.filter({ hasText: 'api' })).toHaveAttribute('data-health', 'degraded')
  await expect(cards.filter({ hasText: 'worker' })).toHaveAttribute('data-health', 'down')
  await expect(cards.filter({ hasText: 'cron' })).toHaveAttribute('data-health', 'idle')
  // the colours are real, not just labels
  const color = (name) => cards.filter({ hasText: name }).getByTestId('health-badge').evaluate((e) => getComputedStyle(e).backgroundColor)
  expect(await color('web')).toBe('rgb(61, 220, 151)')      // green
  expect(await color('api')).toBe('rgb(255, 209, 102)')     // yellow
  expect(await color('worker')).toBe('rgb(255, 107, 107)')  // red
  await expect(cards.filter({ hasText: 'api' }).getByTestId('card-replicas')).toHaveText('1/3 ready')
})

test('summary tiles count each health state', async ({ page }) => {
  await mockApi(page)
  await page.goto('/ui/')
  await expect(page.getByTestId('stat-total')).toHaveText('4')
  await expect(page.getByTestId('stat-healthy')).toHaveText('1')
  await expect(page.getByTestId('stat-degraded')).toHaveText('1')
  await expect(page.getByTestId('stat-down')).toHaveText('1')
  await expect(page.getByTestId('stat-idle')).toHaveText('1')
})

test('namespace selector is filled from the API and filters the list', async ({ page }) => {
  const api = await mockApi(page)
  await page.goto('/ui/')
  await expect(page.getByTestId('namespace-select').locator('option')).toHaveText(['All namespaces', 'team-a', 'team-b'])
  await page.getByTestId('namespace-select').selectOption('team-b')
  await expect(page.getByTestId('deployment-card')).toHaveCount(2)
  await expect(page.getByTestId('deployment-card').first()).toContainText('team-b')
  expect(api.calls.some((c) => c.path === '/api/deployments' && c.search === '?namespace=team-b')).toBe(true)
})

test('search filters by name', async ({ page }) => {
  await mockApi(page)
  await page.goto('/ui/')
  await page.getByPlaceholder('Filter by name').fill('WOR')
  await expect(page.getByTestId('deployment-card')).toHaveCount(1)
  await expect(page.getByTestId('deployment-card')).toContainText('worker')
})

test('shows an empty state', async ({ page }) => {
  await mockApi(page, [])
  await page.goto('/ui/')
  await expect(page.getByTestId('empty')).toBeVisible()
})

test('shows an error banner when the API fails, and recovers on the next poll', async ({ page }) => {
  await page.clock.install()
  const api = await mockApi(page)
  api.forceStatus = { status: 500, message: 'Kubernetes API error' }
  await page.goto('/ui/')
  await expect(page.getByTestId('list-error')).toContainText('Kubernetes API error')
  api.forceStatus = null
  await page.clock.fastForward(5500)
  await expect(page.getByTestId('deployment-card')).toHaveCount(4)
  await expect(page.getByTestId('list-error')).toHaveCount(0)
})

test.describe('polling', () => {
  test('refreshes automatically every 5 seconds without a reload', async ({ page }) => {
    await page.clock.install()
    const api = await mockApi(page)
    await page.goto('/ui/')
    await expect(page.getByTestId('deployment-card')).toHaveCount(4)
    api.items.push(dep(9, 'newcomer', 'team-a', 1, 1))
    api.items.find((d) => d.name === 'worker').replicas.ready = 2
    api.items.find((d) => d.name === 'worker').health = 'healthy'
    await page.clock.fastForward(5500)
    await expect(page.getByTestId('deployment-card')).toHaveCount(5)
    await expect(page.getByTestId('deployment-card').filter({ hasText: 'worker' })).toHaveAttribute('data-health', 'healthy')
  })

  test('a 401 stops polling and sends the user back to sign-in (no request storm)', async ({ page }) => {
    await page.clock.install()
    const api = await mockApi(page)
    await page.goto('/ui/')
    await expect(page.getByTestId('deployment-card')).toHaveCount(4)
    api.forceStatus = { status: 401, message: 'Invalid or expired token' }
    await page.clock.fastForward(5500)
    await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('Sign in again')
    const before = api.calls.length
    await page.clock.fastForward(60_000)
    expect(api.calls.length).toBe(before) // nothing more was sent
  })

  test('a 429 backs off for Retry-After instead of hammering the API', async ({ page }) => {
    await page.clock.install()
    const api = await mockApi(page)
    await page.goto('/ui/')
    await expect(page.getByTestId('deployment-card')).toHaveCount(4)
    api.forceStatus = { status: 429, message: 'Too many requests', headers: { 'Retry-After': '30' } }
    await page.clock.fastForward(5500)
    await expect(page.getByTestId('list-error')).toContainText('Too many requests')
    const before = api.calls.length
    await page.clock.fastForward(20_000)          // still inside the 30s wait
    expect(api.calls.length).toBe(before)
    api.forceStatus = null
    await page.clock.fastForward(15_000)          // past it
    await expect(page.getByTestId('list-error')).toHaveCount(0)
  })
})
