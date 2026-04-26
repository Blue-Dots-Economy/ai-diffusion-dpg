/**
 * dev-kit/frontend/src/crypto.js
 *
 * Browser-side hybrid encryption using the Web Crypto API.
 *
 * Encryption scheme:
 *   1. Fetch the server's RSA-4096 public key (SPKI/DER base64) once.
 *   2. For each secret value:
 *      a. Generate a fresh random AES-256-GCM key.
 *      b. Encrypt the value with AES-256-GCM (12-byte random IV).
 *      c. Encrypt the AES key with RSA-OAEP (SHA-256).
 *      d. Return { encrypted_key, iv, encrypted_value } — all base64.
 *
 * The server decrypts with crypto.py:decrypt_secret().
 */

let _cachedPublicKey = null

/**
 * Whether the Web Crypto SubtleCrypto API is available.
 *
 * `window.crypto.subtle` is only exposed in secure contexts (HTTPS or
 * localhost). When the dev-kit is served over plain HTTP from a remote host
 * (e.g. a VM IP), it is `undefined` and any call to importKey/encrypt throws.
 *
 * @returns {boolean}
 */
export function isSubtleCryptoAvailable() {
  return typeof window !== 'undefined'
    && !!window.crypto
    && !!window.crypto.subtle
    && typeof window.crypto.subtle.importKey === 'function'
}

/**
 * Build the secrets payload for deploy endpoints.
 *
 * In a secure context, encrypts every secret with hybrid RSA-OAEP + AES-GCM
 * and returns `{ encrypted_secrets }`. In a non-secure context where
 * SubtleCrypto is unavailable, falls back to `{ secrets }` (plaintext); the
 * dev-kit backend accepts both shapes.
 *
 * @param {Object} secretsDict - Flat or nested object of secret strings.
 * @returns {Promise<Object>} Either `{ encrypted_secrets }` or `{ secrets }`.
 */
export async function buildSecretsPayload(secretsDict) {
  const dict = secretsDict || {}
  if (isSubtleCryptoAvailable()) {
    return { encrypted_secrets: await encryptSecretsDict(dict) }
  }
  return { secrets: dict }
}

/**
 * Fetch and import the server's RSA public key.
 * Result is cached for the lifetime of the page.
 *
 * @returns {Promise<CryptoKey>} The imported RSA-OAEP public key.
 */
export async function fetchPublicKey() {
  if (_cachedPublicKey) return _cachedPublicKey
  const res = await fetch('/api/deploy/public-key')
  const { public_key } = await res.json()
  const keyBytes = Uint8Array.from(atob(public_key), c => c.charCodeAt(0))
  _cachedPublicKey = await window.crypto.subtle.importKey(
    'spki',
    keyBytes,
    { name: 'RSA-OAEP', hash: 'SHA-256' },
    false,
    ['encrypt']
  )
  return _cachedPublicKey
}

/**
 * Encrypt a single plaintext string with hybrid RSA-OAEP + AES-256-GCM.
 *
 * @param {string} value - The plaintext secret to encrypt.
 * @returns {Promise<{encrypted_key: string, iv: string, encrypted_value: string}>}
 *   All values are base64-encoded strings suitable for JSON serialisation.
 */
export async function encryptSecret(value) {
  const publicKey = await fetchPublicKey()

  // Generate a fresh 256-bit AES key for this secret
  const aesKey = await window.crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    true,
    ['encrypt']
  )
  const aesKeyRaw = await window.crypto.subtle.exportKey('raw', aesKey)

  // Encrypt the secret value with AES-256-GCM
  const iv = window.crypto.getRandomValues(new Uint8Array(12))
  const encryptedValue = await window.crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    aesKey,
    new TextEncoder().encode(value)
  )

  // Encrypt the AES key with RSA-OAEP (the only thing RSA touches)
  const encryptedKey = await window.crypto.subtle.encrypt(
    { name: 'RSA-OAEP' },
    publicKey,
    aesKeyRaw
  )

  const toBase64 = (buf) =>
    btoa(String.fromCharCode(...new Uint8Array(buf)))

  return {
    encrypted_key: toBase64(encryptedKey),
    iv: toBase64(iv),
    encrypted_value: toBase64(encryptedValue),
  }
}

/**
 * Recursively encrypt every non-empty string value in a secrets dict.
 *
 * Nested objects (e.g. tool_secrets) are recursed into.
 * Empty strings are passed through unchanged (no field to encrypt).
 *
 * @param {Object} secretsDict - Flat or nested object of secret strings.
 * @returns {Promise<Object>} Same structure with string values replaced by
 *   cipher-payload objects { encrypted_key, iv, encrypted_value }.
 */
export async function encryptSecretsDict(secretsDict) {
  const result = {}
  for (const [key, value] of Object.entries(secretsDict)) {
    if (typeof value === 'string' && value !== '') {
      result[key] = await encryptSecret(value)
    } else if (value !== null && typeof value === 'object') {
      result[key] = await encryptSecretsDict(value)
    } else {
      result[key] = value  // empty string or null — pass through
    }
  }
  return result
}
