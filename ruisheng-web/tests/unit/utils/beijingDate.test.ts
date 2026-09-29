import { describe, expect, it } from 'vitest'
import { beijingDateString } from '@/utils/beijingDate'

describe('beijingDateString', () => {
  it('uses Beijing midnight rather than UTC midnight', () => {
    expect(beijingDateString(new Date('2026-08-18T15:59:59Z'))).toBe('2026-08-18')
    expect(beijingDateString(new Date('2026-08-18T16:00:00Z'))).toBe('2026-08-19')
  })
})
