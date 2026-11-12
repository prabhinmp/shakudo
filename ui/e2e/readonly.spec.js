import { test, expect } from '@playwright/test'
import { dep, mockApi, signedIn, SEED } from './helpers.js'

// A cluster-wide view: built-in namespaces and deployments we did not create are visible but never editable.
const seed = () => [
  ...SEED(),
  dep(10, 'coredns', 'kube-system', 2, 2, { managed: false, read_only: true }),
  dep(11, 'legacy', 'default', 1, 1, { managed: false, read_only: true }),
]

test('lists every namespace (even empty and built-in ones) and every deployment', async ({ page }) => {
  await signedIn(page, 'admin')
  const api = await mockApi(page, seed())
  api.extraNamespaces = ['empty-ns', 'kube-public']
  await page.goto('/ui/')
  await expect(page.getByTestId('deployment-card')).toHaveCount(6)
  const options = await page.getByTestId('namespace-select').locator('option').allTextContents()
  expect(options).toEqual(expect.arrayContaining(['All namespaces', 'default', 'empty-ns', 'kube-public', 'kube-system', 'team-a', 'team-b']))
  await expect(page.getByTestId('readonly-tag')).toHaveCount(2)
})

for (const name of ['coredns', 'legacy']) {
  test(`${name}: scale, restart and delete are disabled even for an admin`, async ({ page }) => {
    await signedIn(page, 'admin')
    const api = await mockApi(page, seed())
    await page.goto('/ui/')
    await page.getByTestId('deployment-card').filter({ hasText: name }).click()
    await expect(page.getByTestId('readonly-note')).toBeVisible()
    await expect(page.getByLabel('Replicas', { exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Scale' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Restart' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Delete' })).toBeDisabled()
    // still shows the detail: replicas, pods, conditions
    await expect(page.getByTestId('pod-table')).toBeVisible()
    expect(api.calls.filter((c) => c.method !== 'GET')).toHaveLength(0)
  })
}

test('an app-created deployment is still fully actionable', async ({ page }) => {
  await signedIn(page, 'admin')
  await mockApi(page, seed())
  await page.goto('/ui/')
  await page.getByTestId('deployment-card').filter({ hasText: 'web' }).click()
  await expect(page.getByTestId('readonly-note')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Scale' })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Delete' })).toBeEnabled()
})
