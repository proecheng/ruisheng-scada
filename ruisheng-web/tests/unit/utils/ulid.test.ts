import { describe, it, expect } from 'vitest'
import { generateUlid } from '@/utils/ulid'

describe('generateUlid', () => {
  it('returns a 26-character Crockford Base32 string', () => {
    const id = generateUlid()
    expect(id).toHaveLength(26)
    expect(id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/)
  })

  it('produces monotonically sortable IDs in the same millisecond', () => {
    const a = generateUlid()
    const b = generateUlid()
    expect(a < b || a === b).toBe(true)
  })

  it('produces distinct IDs across calls', () => {
    const ids = new Set(Array.from({ length: 100 }, () => generateUlid()))
    expect(ids.size).toBe(100)
  })
})
