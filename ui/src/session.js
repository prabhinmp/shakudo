// Token handling. The token lives in sessionStorage only (cleared when the tab closes).
// We decode the payload ONLY to show the user's name/role and hide buttons they can't use;
// the API is what actually enforces permissions, so a forged token here gains nothing.
const KEY = 'deploy-ui.token'

export function decodeToken(token) {
  try {
    const part = token.split('.')[1]
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '='))
    const c = JSON.parse(json)
    if (!c || typeof c.role !== 'string' || typeof c.exp !== 'number') return null
    return { sub: String(c.sub ?? 'unknown'), role: c.role, exp: c.exp, namespaces: c.namespaces ?? [] }
  } catch {
    return null
  }
}

export const isExpired = (s) => s.exp * 1000 <= Date.now()

export const loadToken = () => {
  try { return sessionStorage.getItem(KEY) } catch { return null }
}
export const saveToken = (t) => {
  try { sessionStorage.setItem(KEY, t) } catch { /* storage blocked: stay signed in for this page only */ }
}
export const clearToken = () => {
  try { sessionStorage.removeItem(KEY) } catch { /* ignore */ }
}

const RANK = { viewer: 1, operator: 2, admin: 3 }
export const can = (role, minimum) => (RANK[role] ?? 0) >= RANK[minimum]
