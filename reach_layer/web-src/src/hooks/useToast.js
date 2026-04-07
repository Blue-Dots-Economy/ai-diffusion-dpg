import { useState, useCallback } from 'react'
import { generateUUID } from '../utils'

/**
 * Manage a queue of toast notifications.
 * @returns {{ toasts: Array, addToast: (msg, type?) => void, removeToast: (id) => void }}
 */
export function useToast() {
  const [toasts, setToasts] = useState([])

  const removeToast = useCallback(id => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const addToast = useCallback(
    (message, type = 'error') => {
      const id = generateUUID()
      setToasts(prev => [...prev, { id, message, type }])
      setTimeout(() => removeToast(id), 4000)
    },
    [removeToast]
  )

  return { toasts, addToast, removeToast }
}
