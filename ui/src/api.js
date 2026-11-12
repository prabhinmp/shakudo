// Thin client for the Deployment Manager API. Every call sends the bearer token and turns
// non-2xx answers into an ApiError carrying the API's own message.
export class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

export function createApi(getToken) {
  async function request(path, { method = 'GET', body, signal } = {}) {
    let res
    try {
      res = await fetch(path, {
        method,
        signal,
        headers: {
          Authorization: `Bearer ${getToken()}`,
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
    } catch (e) {
      if (e.name === 'AbortError') throw e
      throw new ApiError(0, 'Cannot reach the API. Check your connection.')
    }
    if (res.status === 204) return null
    let data = null
    try { data = await res.json() } catch { /* non-JSON body */ }
    if (!res.ok) {
      const retry = Number(res.headers.get('Retry-After')) || undefined
      throw new ApiError(res.status, data?.error?.message || `Request failed (${res.status})`, retry)
    }
    return data
  }

  return {
    listDeployments: (namespace, signal) =>
      request(`/api/deployments${namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''}`, { signal }),
    listNamespaces: (signal) => request('/api/namespaces', { signal }),
    getDeployment: (id, signal) => request(`/api/deployments/${encodeURIComponent(id)}`, { signal }),
    scale: (id, replicas) => request(`/api/deployments/${encodeURIComponent(id)}`, { method: 'PATCH', body: { replicas } }),
    restart: (id) => request(`/api/deployments/${encodeURIComponent(id)}/restart`, { method: 'POST' }),
    remove: (id) => request(`/api/deployments/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  }
}
