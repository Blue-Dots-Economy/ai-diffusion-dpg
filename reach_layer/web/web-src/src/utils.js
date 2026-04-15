/**
 * Generate a UUID v4. Uses crypto.randomUUID when available.
 * @returns {string} UUID string
 */
export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/**
 * Format a timestamp string or Date to HH:MM.
 * @param {string|Date|null} ts
 * @returns {string}
 */
export function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/**
 * Format a timestamp string or Date to full locale string.
 * @param {string|Date|null} ts
 * @returns {string}
 */
export function formatFullTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString()
}
