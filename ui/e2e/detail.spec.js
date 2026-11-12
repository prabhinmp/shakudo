import { test, expect } from '@playwright/test'
import { ID, mockApi, signedIn } from './helpers.js'

test('click a deployment to see replicas, pods and conditions', async ({ page }) => {
  await signedIn(page, 'viewer')
  await mockApi(page)
  await page.goto('/ui/')
  await page.getByTestId('deployment-card').filter({ hasText: 'api' }).click()
  const panel = page.getByTestId('detail')
  await expect(page.getByTestId('detail-name')).toHaveText('api')
  await expect(page).toHaveURL(new RegExp(`#/d/${ID(2)}$`))
  await expect(page.getByTestId('replicas-desired')).toHaveText('3')
  await expect(page.getByTestId('replicas-ready')).toHaveText('1')
  await expect(page.getByTestId('replicas-unavailable')).toHaveText('2')
  const rows = panel.getByTestId('pod-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toContainText('Running')
  await expect(rows.nth(1)).toContainText('CrashLoopBackOff')
  await expect(rows.nth(1)).toContainText('4')
  const conds = panel.getByTestId('condition')
  await expect(conds).toHaveCount(2)
  await expect(conds.nth(0)).toContainText('Available: True')
  await expect(conds.nth(1)).toContainText('ProgressDeadlineExceeded')
})

test('a detail URL opens directly and the close button returns to the dashboard', async ({ page }) => {
  await signedIn(page, 'viewer')
  await mockApi(page)
  await page.goto(`/ui/#/d/${ID(1)}`)
  await expect(page.getByTestId('detail-name')).toHaveText('web')
  await page.getByRole('button', { name: 'Close details' }).click()
  await expect(page.getByTestId('detail')).toHaveCount(0)
})

test('detail view auto-refreshes', async ({ page }) => {
  await page.clock.install()
  await signedIn(page, 'viewer')
  const api = await mockApi(page)
  await page.goto(`/ui/#/d/${ID(1)}`)
  await expect(page.getByTestId('replicas-ready')).toHaveText('3')
  api.items[0].replicas.ready = 1
  await page.clock.fastForward(5500)
  await expect(page.getByTestId('replicas-ready')).toHaveText('1')
})

test('a deployment that vanished closes the panel with a message', async ({ page }) => {
  await signedIn(page, 'viewer')
  await mockApi(page)
  await page.goto(`/ui/#/d/${ID(99)}`)
  await expect(page.getByTestId('toast')).toContainText('no longer exists')
  await expect(page.getByTestId('detail')).toHaveCount(0)
})

test('a deployment with no pods says so', async ({ page }) => {
  await signedIn(page, 'viewer')
  const api = await mockApi(page)
  api.detailOverride = { ...api.items[3], pods: [], conditions: [] }
  await page.goto(`/ui/#/d/${ID(4)}`)
  await expect(page.getByTestId('no-pods')).toBeVisible()
})
