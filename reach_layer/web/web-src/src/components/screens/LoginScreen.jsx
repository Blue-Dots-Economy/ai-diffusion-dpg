import { useEffect, useRef, useState } from 'react'
import { exchangeGoogleCredential } from '../../api'

/**
 * Google Sign-In screen.
 * Loads the Google Identity Services script, renders the official
 * "Sign in with Google" button, and on success exchanges the returned
 * ID token for a server-side HttpOnly session cookie via POST /auth/google.
 *
 * @param {{
 *   config: Object,
 *   onSignedIn: (identity: { user_id: string, display_name: string, email: string, picture: string }) => void,
 *   onError?: (msg: string) => void,
 * }} props
 */
export function LoginScreen({ config, onSignedIn, onError }) {
  const buttonRef = useRef(null)
  const [busy, setBusy] = useState(false)
  const clientId = config.auth?.google_client_id

  useEffect(() => {
    if (!clientId) {
      onError?.('Google sign-in is not configured (missing client_id).')
      return
    }
    let cancelled = false

    const handleCredential = async (resp) => {
      if (cancelled) return
      setBusy(true)
      try {
        const identity = await exchangeGoogleCredential(resp.credential)
        if (!cancelled) onSignedIn(identity)
      } catch (e) {
        onError?.('Sign-in failed. Please try again.')
        setBusy(false)
      }
    }

    const init = () => {
      if (cancelled || !window.google?.accounts?.id) return
      window.google.accounts.id.initialize({
        client_id: clientId,
        callback: handleCredential,
        ux_mode: 'popup',
        auto_select: false,
      })
      if (buttonRef.current) {
        buttonRef.current.innerHTML = ''
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: 'filled_blue',
          size: 'large',
          shape: 'pill',
          text: 'continue_with',
          width: 280,
        })
      }
    }

    // Inject GIS script once
    const SCRIPT_ID = 'gis-script'
    let script = document.getElementById(SCRIPT_ID)
    if (!script) {
      script = document.createElement('script')
      script.id = SCRIPT_ID
      script.src = 'https://accounts.google.com/gsi/client'
      script.async = true
      script.defer = true
      script.onload = init
      document.head.appendChild(script)
    } else if (window.google?.accounts?.id) {
      init()
    } else {
      script.addEventListener('load', init)
    }

    return () => {
      cancelled = true
    }
  }, [clientId, onSignedIn, onError])

  return (
    <div className="flex items-center justify-center h-full bg-[var(--bg)] px-6">
      <div className="w-full max-w-sm bg-[var(--surface)] border border-[var(--border)] rounded-2xl p-8 shadow-2xl">
        <div className="flex items-center gap-3 mb-7">
          <div className="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center text-lg">
            {config.app_icon}
          </div>
          <div>
            <div className="font-bold text-[var(--text)] leading-tight">{config.app_name}</div>
            <div className="text-[11px] text-gray-500 mt-0.5">{config.app_tagline}</div>
          </div>
        </div>

        <h1 className="text-xl font-bold text-[var(--text)] mb-1.5">
          {config.setup_heading}
        </h1>
        <p className="text-sm text-gray-500 mb-6 leading-relaxed">
          Sign in with your Google account to begin or resume your conversation.
        </p>

        <div className="flex items-center justify-center min-h-[44px]">
          {busy ? (
            <div className="text-sm text-gray-500">Signing you in…</div>
          ) : (
            <div ref={buttonRef} />
          )}
        </div>

        {!clientId && (
          <p className="text-xs text-red-500 mt-4 text-center">
            Google sign-in is not configured. Ask the administrator to set
            GOOGLE_CLIENT_ID.
          </p>
        )}
      </div>
    </div>
  )
}
