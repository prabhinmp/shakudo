import { useEffect, useRef, useState } from 'react'

// Deleting is irreversible, so the user must type the deployment's name to enable the button.
export default function ConfirmDelete({ name, busy, onCancel, onConfirm }) {
  const [typed, setTyped] = useState('')
  const input = useRef(null)
  useEffect(() => input.current?.focus(), [])

  return (
    <div className="overlay" onKeyDown={(e) => e.key === 'Escape' && onCancel()}>
      <div className="glass card modal" role="alertdialog" aria-labelledby="del-title" data-testid="confirm-delete">
        <h3 id="del-title">Delete “{name}”?</h3>
        <p className="muted">This removes the deployment and all its pods. It cannot be undone.</p>
        <label htmlFor="confirm-name">Type <b>{name}</b> to confirm</label>
        <input id="confirm-name" ref={input} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        <div className="row actions">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button className="btn danger" disabled={typed !== name || busy} onClick={onConfirm}>Delete permanently</button>
        </div>
      </div>
    </div>
  )
}
