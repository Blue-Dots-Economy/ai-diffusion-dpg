import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---------------------------------------------------------------------------
// Mock SubtleCrypto
// We cannot run real RSA/AES in a pure Node test environment without a DOM.
// We verify the orchestration logic (call order, structure) with mocks.
// ---------------------------------------------------------------------------

const mockAesKey = { type: 'secret', algorithm: { name: 'AES-GCM' } }
const mockPublicKey = { type: 'public', algorithm: { name: 'RSA-OAEP' } }
const mockEncryptedKey = new ArrayBuffer(512)      // RSA-4096 → 512 bytes
const mockIv = new Uint8Array(12).fill(7)
const mockEncryptedValue = new ArrayBuffer(32)

const mockSubtle = {
  importKey: vi.fn().mockResolvedValue(mockPublicKey),
  generateKey: vi.fn().mockResolvedValue(mockAesKey),
  exportKey: vi.fn().mockResolvedValue(new ArrayBuffer(32)),  // 32-byte AES key
  encrypt: vi.fn()
    .mockResolvedValueOnce(mockEncryptedValue)   // AES-GCM call (value)
    .mockResolvedValueOnce(mockEncryptedKey),    // RSA-OAEP call (AES key)
}

const mockGetRandomValues = vi.fn((arr) => { arr.fill(7); return arr })

vi.stubGlobal('window', {
  crypto: { subtle: mockSubtle, getRandomValues: mockGetRandomValues },
})

global.fetch = vi.fn().mockResolvedValue({
  json: () => Promise.resolve({ public_key: btoa('fake-spki-bytes') }),
})

describe('crypto.js', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset module so _cachedKey is cleared
    vi.resetModules()
    // Re-stub after reset
    mockSubtle.encrypt
      .mockResolvedValueOnce(mockEncryptedValue)
      .mockResolvedValueOnce(mockEncryptedKey)
  })

  it('fetchPublicKey fetches /api/deploy/public-key and imports as RSA-OAEP', async () => {
    const { fetchPublicKey } = await import('./crypto.js')
    const key = await fetchPublicKey()
    expect(global.fetch).toHaveBeenCalledWith('/api/deploy/public-key')
    expect(mockSubtle.importKey).toHaveBeenCalledWith(
      'spki',
      expect.any(Uint8Array),
      { name: 'RSA-OAEP', hash: 'SHA-256' },
      false,
      ['encrypt']
    )
    expect(key).toBe(mockPublicKey)
  })

  it('encryptSecret returns object with encrypted_key, iv, encrypted_value', async () => {
    const { encryptSecret } = await import('./crypto.js')
    const result = await encryptSecret('sk-ant-secret')
    expect(result).toHaveProperty('encrypted_key')
    expect(result).toHaveProperty('iv')
    expect(result).toHaveProperty('encrypted_value')
    // All values must be base64 strings
    expect(typeof result.encrypted_key).toBe('string')
    expect(typeof result.iv).toBe('string')
    expect(typeof result.encrypted_value).toBe('string')
  })

  it('encryptSecret generates a fresh AES key and 12-byte IV per call', async () => {
    const { encryptSecret } = await import('./crypto.js')
    await encryptSecret('value')
    expect(mockSubtle.generateKey).toHaveBeenCalledWith(
      { name: 'AES-GCM', length: 256 }, true, ['encrypt']
    )
    expect(mockGetRandomValues).toHaveBeenCalledWith(expect.any(Uint8Array))
    const ivArg = mockGetRandomValues.mock.calls[0][0]
    expect(ivArg.length).toBe(12)
  })

  it('encryptSecretsDict encrypts every non-empty string value', async () => {
    mockSubtle.encrypt
      .mockResolvedValue(mockEncryptedValue)  // reset to always resolve
    const { encryptSecretsDict } = await import('./crypto.js')
    const input = {
      anthropic_api_key: 'sk-ant',
      redis_password: '',
      tool_secrets: { ONEST_API_KEY: 'onest-key' },
    }
    const result = await encryptSecretsDict(input)
    // non-empty strings are cipher objects
    expect(result.anthropic_api_key).toHaveProperty('encrypted_key')
    expect(result.tool_secrets.ONEST_API_KEY).toHaveProperty('encrypted_key')
    // empty string passes through unchanged
    expect(result.redis_password).toBe('')
  })
})
