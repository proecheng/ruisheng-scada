import { describe, it, expect } from 'vitest'
import { useAsync } from '@/composables/useAsync'

describe('useAsync', () => {
  it('starts idle', () => {
    const a = useAsync(() => Promise.resolve('x'))
    expect(a.state.value).toBe('idle')
    expect(a.data.value).toBeNull()
    expect(a.error.value).toBeNull()
  })

  it('transitions idle → pending → success on resolve', async () => {
    const a = useAsync(() => Promise.resolve(42))
    const p = a.run()
    expect(a.state.value).toBe('pending')
    await p
    expect(a.state.value).toBe('success')
    expect(a.data.value).toBe(42)
  })

  it('transitions to error on reject', async () => {
    const a = useAsync(() => Promise.reject(new Error('boom')))
    await expect(a.run()).rejects.toThrow('boom')
    expect(a.state.value).toBe('error')
    expect(a.error.value?.message).toBe('boom')
  })

  it('reset clears state', async () => {
    const a = useAsync(() => Promise.resolve(1))
    await a.run()
    a.reset()
    expect(a.state.value).toBe('idle')
    expect(a.data.value).toBeNull()
  })
})
