import { useState } from 'react'
import { decodeToken, isExpired } from '../session.js'

export default function SignIn({ onSignIn, notice }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  const submit = (e) => {
    e.preventDefault()
    const token = value.trim()
    const s = decodeToken(token)
    if (!s) return setError('That does not look like a valid API token.')
    if (isExpired(s)) return setError('That token has expired. Mint a new one.')
    onSignIn(token)
  }

  return (
    <main className="center">
      <form className="glass card signin" onSubmit={submit}>
        <h1>Deployment Manager</h1>
        <p className="muted">Paste your API token to continue. It stays in this tab only.</p>
        {notice && <p className="banner warn" role="alert">{notice}</p>}
        <label htmlFor="token">API token</label>
        <textarea id="token" rows={4} value={value} spellCheck={false} autoComplete="off"
          onChange={(e) => { setValue(e.target.value); setError('') }} />
        {error && <p className="banner bad" role="alert">{error}</p>}
        <button className="btn primary" type="submit">Sign in</button>
      </form>
    </main>
  )
}
