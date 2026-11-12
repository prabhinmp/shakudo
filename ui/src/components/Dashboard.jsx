import { useEffect, useMemo, useState } from 'react'
import { usePolling } from '../usePolling.js'
import Badge from './Badge.jsx'
import DetailPanel from './DetailPanel.jsx'
import Toasts, { useToasts } from './Toasts.jsx'

const idFromHash = () => {
  const m = window.location.hash.match(/^#\/d\/([0-9a-f-]{36})$/)
  return m ? m[1] : null
}

export default function Dashboard({ api, session, onSignOut }) {
  const [namespace, setNamespace] = useState('')
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState(idFromHash)
  const { toasts, push } = useToasts()
  const expired = () => onSignOut('Your session expired or the token was rejected. Sign in again.')

  useEffect(() => {
    const on = () => setSelectedId(idFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const list = usePolling(
    async (signal) => {
      const [deployments, ns] = await Promise.all([api.listDeployments(namespace, signal), api.listNamespaces(signal)])
      return { deployments, namespaces: ns.namespaces }
    },
    [api, namespace],
    { onUnauthorized: expired },
  )

  const deployments = list.data?.deployments ?? []
  const namespaces = list.data?.namespaces ?? []
  const shown = useMemo(
    () => deployments.filter((d) => d.name.toLowerCase().includes(search.toLowerCase())),
    [deployments, search],
  )
  const counts = useMemo(() => {
    const c = { healthy: 0, degraded: 0, down: 0, idle: 0 }
    for (const d of deployments) c[d.health] = (c[d.health] ?? 0) + 1
    return c
  }, [deployments])

  const open = (id) => { window.location.hash = `#/d/${id}` }
  const close = () => { history.pushState(null, '', window.location.pathname + window.location.search); setSelectedId(null) }

  return (
    <div className="shell">
      <header className="glass topbar">
        <h1>Deployment Manager</h1>
        <div className="grow" />
        <span className="muted" data-testid="who">{session.sub} · <b>{session.role}</b></span>
        <button className="btn" onClick={() => onSignOut()}>Sign out</button>
      </header>

      <section className="stats" aria-label="Summary">
        {[['Total', deployments.length, 'total'], ['Healthy', counts.healthy, 'green'], ['Degraded', counts.degraded, 'yellow'],
          ['Down', counts.down, 'red'], ['Idle', counts.idle, 'grey']].map(([label, n, tone]) => (
          <div key={label} className={`glass stat ${tone}`}><span className="n" data-testid={`stat-${label.toLowerCase()}`}>{n}</span><span>{label}</span></div>
        ))}
      </section>

      <section className="glass toolbar">
        <label className="field">Namespace
          <select value={namespace} onChange={(e) => setNamespace(e.target.value)} data-testid="namespace-select">
            <option value="">All namespaces</option>
            {namespace && !namespaces.includes(namespace) && <option value={namespace}>{namespace}</option>}
            {namespaces.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="field grow">Search
          <input type="search" placeholder="Filter by name" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <div className="refresh">
          <span className="muted" data-testid="last-updated">
            {list.lastUpdated ? `Updated ${list.lastUpdated.toLocaleTimeString()} · auto-refresh 5s` : 'Loading…'}
          </span>
          <button className="btn" onClick={list.refresh}>Refresh</button>
        </div>
      </section>

      {list.error && <p className="banner bad" role="alert" data-testid="list-error">{list.error.message}</p>}

      {list.lastUpdated && shown.length === 0 && !list.error && (
        <p className="glass card empty" data-testid="empty">No deployments found.</p>
      )}

      <section className="grid" aria-label="Deployments">
        {shown.map((d) => (
          <button key={d.id} className="glass card dep" data-testid="deployment-card" data-health={d.health} onClick={() => open(d.id)}>
            <div className="row"><strong className="name">{d.name}</strong><Badge health={d.health} /></div>
            <div className="muted small">{d.namespace}{d.read_only && <span className="tag" data-testid="readonly-tag" title="Visible only: scale, restart and delete are disabled"> · read-only</span>}</div>
            <div className="muted small ellipsis" title={d.image}>{d.image}</div>
            <div className="replicas" data-testid="card-replicas">{d.replicas.ready}/{d.replicas.desired} ready</div>
          </button>
        ))}
      </section>

      {selectedId && (
        <DetailPanel
          key={selectedId} id={selectedId} api={api} session={session}
          onClose={close} onUnauthorized={expired} toast={push} onChanged={list.refresh}
        />
      )}
      <Toasts toasts={toasts} />
    </div>
  )
}
