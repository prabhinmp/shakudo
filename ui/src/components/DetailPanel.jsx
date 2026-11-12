import { useState } from 'react'
import { usePolling } from '../usePolling.js'
import { can } from '../session.js'
import Badge from './Badge.jsx'
import ConfirmDelete from './ConfirmDelete.jsx'

const MAX_REPLICAS = 20 // matches the API default cap; the API still has the final say

export default function DetailPanel({ id, api, session, onClose, onUnauthorized, toast, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [replicas, setReplicas] = useState('')
  const [scaleError, setScaleError] = useState('')

  const detail = usePolling((signal) => api.getDeployment(id, signal), [api, id], {
    onUnauthorized,
    onNotFound: () => { toast('That deployment no longer exists.', 'bad'); onClose() },
  })
  const d = detail.data
  const canOperate = can(session.role, 'operator')
  const canDelete = can(session.role, 'admin')

  const act = async (fn, okMessage) => {
    setBusy(true)
    try {
      await fn()
      toast(okMessage)
      detail.refresh()
      onChanged()
      return true
    } catch (e) {
      if (e.status === 401) onUnauthorized()
      else toast(e.message, 'bad')
      return false
    } finally {
      setBusy(false)
    }
  }

  const scale = (e) => {
    e.preventDefault()
    const n = Number(replicas)
    if (replicas === '' || !Number.isInteger(n) || n < 0 || n > MAX_REPLICAS) {
      return setScaleError(`Enter a whole number from 0 to ${MAX_REPLICAS}.`)
    }
    setScaleError('')
    act(() => api.scale(id, n), `Scaled to ${n} replica${n === 1 ? '' : 's'}.`)
  }

  const remove = async () => {
    if (await act(() => api.remove(id), 'Deployment deleted.')) { setConfirming(false); onClose() }
  }

  return (
    <aside className="glass panel" role="dialog" aria-label="Deployment details" data-testid="detail">
      <div className="row">
        <h2 data-testid="detail-name">{d ? d.name : 'Loading…'}</h2>
        <button className="btn" onClick={onClose} aria-label="Close details">✕</button>
      </div>
      {detail.error && <p className="banner bad" role="alert">{detail.error.message}</p>}
      {d && (
        <>
          <div className="row"><Badge health={d.health} /><span className="muted small">{d.namespace}</span></div>
          <p className="muted small ellipsis" title={d.image}>{d.image}</p>

          <h3>Replicas</h3>
          <dl className="kv" data-testid="replica-counts">
            {Object.entries(d.replicas).map(([k, v]) => <div key={k}><dt>{k}</dt><dd data-testid={`replicas-${k}`}>{v}</dd></div>)}
          </dl>

          <h3>Actions</h3>
          {!canOperate && <p className="muted small">Your role ({session.role}) is read-only.</p>}
          <form className="scale" onSubmit={scale}>
            <label htmlFor="replicas">Replicas</label>
            <input id="replicas" type="number" min="0" max={MAX_REPLICAS} value={replicas}
              placeholder={String(d.replicas.desired)} onChange={(e) => setReplicas(e.target.value)} disabled={!canOperate || busy} />
            <button className="btn primary" type="submit" disabled={!canOperate || busy}>Scale</button>
          </form>
          {scaleError && <p className="banner bad" role="alert" data-testid="scale-error">{scaleError}</p>}
          <div className="row actions">
            <button className="btn" disabled={!canOperate || busy} onClick={() => act(() => api.restart(id), 'Rolling restart started.')}>Restart</button>
            <button className="btn danger" disabled={!canDelete || busy} onClick={() => setConfirming(true)}
              title={canDelete ? '' : 'Requires the admin role'}>Delete</button>
          </div>

          <h3>Pods</h3>
          {d.pods.length === 0 ? <p className="muted small" data-testid="no-pods">No pods.</p> : (
            <div className="tablewrap">
              <table data-testid="pod-table">
                <thead><tr><th>Pod</th><th>Phase</th><th>Restarts</th><th>Containers</th></tr></thead>
                <tbody>
                  {d.pods.map((p) => (
                    <tr key={p.name} data-testid="pod-row">
                      <td className="ellipsis" title={p.name}>{p.name}</td>
                      <td><span className={`dot ${p.phase === 'Running' ? 'green' : p.phase === 'Pending' ? 'yellow' : 'red'}`} />{p.phase}</td>
                      <td>{p.restart_count}</td>
                      <td>{p.container_statuses.map((c) => `${c.name}: ${c.state}`).join(', ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3>Conditions</h3>
          {d.conditions.length === 0 ? <p className="muted small">None reported.</p> : (
            <ul className="conditions" data-testid="conditions">
              {d.conditions.map((c) => (
                <li key={c.type} data-testid="condition">
                  <span className={`dot ${c.status === 'True' ? 'green' : 'red'}`} /><b>{c.type}</b>: {c.status}
                  {c.reason && <span className="muted"> · {c.reason}</span>}
                  {c.message && <div className="muted small">{c.message}</div>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {confirming && d && <ConfirmDelete name={d.name} busy={busy} onCancel={() => setConfirming(false)} onConfirm={remove} />}
    </aside>
  )
}
