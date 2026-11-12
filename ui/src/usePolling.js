import { useCallback, useEffect, useRef, useState } from 'react'

export const POLL_MS = 5000

/**
 * Calls `load(signal)` now and then every `intervalMs`.
 *  - pauses while the tab is hidden (saves API rate-limit budget) and refreshes on return
 *  - on 401 it STOPS and calls onUnauthorized (repeated bad tokens would lock the shared IP out)
 *  - on 429 it waits for Retry-After before the next try
 * Returns { data, error, lastUpdated, refresh }.
 */
export function usePolling(load, deps, { intervalMs = POLL_MS, onUnauthorized, onNotFound } = {}) {
  const [state, setState] = useState({ data: null, error: null, lastUpdated: null })
  const loadRef = useRef(load)
  loadRef.current = load
  const trigger = useRef(() => {})

  useEffect(() => {
    let stopped = false
    let timer
    let controller
    setState({ data: null, error: null, lastUpdated: null })

    const run = async () => {
      clearTimeout(timer)
      if (stopped) return
      if (document.hidden) { timer = setTimeout(run, intervalMs); return }
      controller?.abort()
      controller = new AbortController()
      let wait = intervalMs
      try {
        const data = await loadRef.current(controller.signal)
        if (!stopped) setState({ data, error: null, lastUpdated: new Date() })
      } catch (e) {
        if (e.name === 'AbortError' || stopped) return
        if (e.status === 401) { stopped = true; onUnauthorized?.(); return }
        if (e.status === 404 && onNotFound) { stopped = true; onNotFound(); return }
        if (e.status === 429) wait = Math.max(wait, (e.retryAfter ?? 60) * 1000)
        setState((s) => ({ ...s, error: e }))
      }
      if (!stopped) timer = setTimeout(run, wait)
    }

    trigger.current = run
    run()
    const onVisible = () => { if (!document.hidden) run() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      clearTimeout(timer)
      controller?.abort()
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  const refresh = useCallback(() => trigger.current(), [])
  return { ...state, refresh }
}
