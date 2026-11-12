// An in-memory fake of the Deployment Manager API, plugged in with page.route().
// It enforces nothing itself (the real API does that); it records calls so tests can assert on them.
export const ID = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

export function makeToken(role = 'admin', { expInSeconds = 3600, sub = 'alice' } = {}) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
  const exp = Math.floor(Date.now() / 1000) + expInSeconds
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub, role, exp, namespaces: ['*'] })}.sig`
}

export const dep = (n, name, namespace, desired, ready, extra = {}) => ({
  id: ID(n), name, namespace, image: `nginx:${n}`, labels: { app: name },
  replicas: { desired, ready, available: ready, unavailable: Math.max(desired - ready, 0) },
  health: desired === 0 ? 'idle' : ready === 0 ? 'down' : ready < desired ? 'degraded' : 'healthy',
  created_at: '2026-01-01T00:00:00Z', managed: true, read_only: false, ...extra,
})

export const SEED = () => [
  dep(1, 'web', 'team-a', 3, 3),
  dep(2, 'api', 'team-a', 3, 1),
  dep(3, 'worker', 'team-b', 2, 0),
  dep(4, 'cron', 'team-b', 0, 0),
]

export const detailOf = (d, over = {}) => ({
  ...d,
  pods: [
    { name: `${d.name}-abc12`, phase: 'Running', restart_count: 0,
      container_statuses: [{ name: d.name, ready: true, restart_count: 0, state: 'running' }] },
    { name: `${d.name}-def34`, phase: 'Pending', restart_count: 4,
      container_statuses: [{ name: d.name, ready: false, restart_count: 4, state: 'waiting: CrashLoopBackOff' }] },
  ],
  conditions: [
    { type: 'Available', status: 'True', reason: 'MinimumReplicasAvailable', message: 'Deployment has minimum availability.' },
    { type: 'Progressing', status: 'False', reason: 'ProgressDeadlineExceeded', message: 'ReplicaSet timed out' },
  ],
  ...over,
})

/** Install the fake API on a page. Returns { state, calls, fail } to inspect and steer it. */
export async function mockApi(page, initial = SEED()) {
  const api = { items: initial, calls: [], failNext: null, detailOverride: null }
  await page.route('**/api/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const path = url.pathname
    api.calls.push({ method: req.method(), path, search: url.search, body: req.postDataJSON?.() ?? null, auth: req.headers().authorization })
    const json = (status, body, headers = {}) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body), headers })
    if (api.failNext && req.method() === api.failNext.method) {
      const f = api.failNext; api.failNext = null
      return json(f.status, { error: { code: 'x', message: f.message } }, f.headers)
    }
    if (api.forceStatus) return json(api.forceStatus.status, { error: { code: 'x', message: api.forceStatus.message } }, api.forceStatus.headers)

    if (path === '/api/namespaces') return json(200, { namespaces: [...new Set([...api.items.map((d) => d.namespace), ...(api.extraNamespaces ?? [])])].sort() })
    if (path === '/api/deployments' && req.method() === 'GET') {
      const ns = url.searchParams.get('namespace')
      return json(200, api.items.filter((d) => !ns || d.namespace === ns))
    }
    const m = path.match(/^\/api\/deployments\/([^/]+)(\/restart)?$/)
    if (m) {
      const d = api.items.find((x) => x.id === m[1])
      if (!d) return json(404, { error: { code: 'not_found', message: 'Deployment not found' } })
      if (req.method() === 'GET') return json(200, api.detailOverride ?? detailOf(d))
      if (req.method() === 'PATCH') { d.replicas.desired = req.postDataJSON().replicas; return json(200, d) }
      if (req.method() === 'POST') return json(200, d)
      if (req.method() === 'DELETE') { api.items = api.items.filter((x) => x !== d); return route.fulfill({ status: 204 }) }
    }
    return json(500, { error: { code: 'x', message: `unmocked ${req.method()} ${path}` } })
  })
  api.count = (method, pathRe) => api.calls.filter((c) => c.method === method && pathRe.test(c.path)).length
  return api
}

/** Put a token in sessionStorage before the app loads, so tests skip the sign-in screen. */
export const signedIn = (page, role = 'admin') =>
  page.addInitScript((t) => sessionStorage.setItem('deploy-ui.token', t), makeToken(role))
