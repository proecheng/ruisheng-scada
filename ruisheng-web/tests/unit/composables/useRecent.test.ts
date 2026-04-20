import { describe, it, expect, beforeEach } from 'vitest'
import { useRecent } from '@/composables/useRecent'

describe('useRecent', () => {
  beforeEach(() => localStorage.clear())

  it('push adds item to front', () => {
    const r = useRecent<string>('test', 5)
    r.push('a')
    r.push('b')
    expect(r.items.value).toEqual(['b', 'a'])
  })

  it('deduplicates — pushing same value moves to front', () => {
    const r = useRecent<string>('test', 5)
    r.push('a')
    r.push('b')
    r.push('a')
    expect(r.items.value).toEqual(['a', 'b'])
  })

  it('respects maxItems', () => {
    const r = useRecent<string>('test', 3)
    r.push('1'); r.push('2'); r.push('3'); r.push('4')
    expect(r.items.value).toEqual(['4', '3', '2'])
  })

  it('persists to localStorage', () => {
    const r1 = useRecent<string>('test', 5)
    r1.push('x')
    const r2 = useRecent<string>('test', 5)
    expect(r2.items.value).toEqual(['x'])
  })
})
