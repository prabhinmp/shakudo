import { useCallback, useMemo, useState } from 'react'
import { createApi } from './api.js'
import { clearToken, decodeToken, isExpired, loadToken, saveToken } from './session.js'
import SignIn from './components/SignIn.jsx'
import Dashboard from './components/Dashboard.jsx'

export default function App() {
  const [token, setToken] = useState(() => {
    const t = loadToken()
    const s = t && decodeToken(t)
    return s && !isExpired(s) ? t : null
  })
  const [notice, setNotice] = useState('')
  const session = useMemo(() => (token ? decodeToken(token) : null), [token])
  const api = useMemo(() => createApi(() => token), [token])

  const signOut = useCallback((message = '') => {
    clearToken()
    setToken(null)
    setNotice(message)
  }, [])

  const signIn = (t) => {
    saveToken(t)
    setNotice('')
    setToken(t)
  }

  return (
    <>
      <div className="bg-orb orb-a" aria-hidden="true" />
      <div className="bg-orb orb-b" aria-hidden="true" />
      {session ? (
        <Dashboard api={api} session={session} onSignOut={signOut} />
      ) : (
        <SignIn onSignIn={signIn} notice={notice} />
      )}
    </>
  )
}
