import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useToast, useToastStore } from '@/composables/useToast'

describe('useToast', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  it('success adds a toast with type success', () => {
    const { success } = useToast()
    success('saved')
    const store = useToastStore()
    expect(store.toasts).toHaveLength(1)
    expect(store.toasts[0]?.type).toBe('success')
    expect(store.toasts[0]?.message).toBe('saved')
  })

  it('success auto-dismisses after 2000ms', async () => {
    const { success } = useToast()
    success('gone')
    const store = useToastStore()
    expect(store.toasts).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(2100)
    expect(store.toasts).toHaveLength(0)
  })

  it('error toast does not auto-dismiss', async () => {
    const { error } = useToast()
    error('persist')
    const store = useToastStore()
    await vi.advanceTimersByTimeAsync(10000)
    expect(store.toasts).toHaveLength(1)
  })

  it('dismiss removes toast by id', () => {
    const { success, dismiss } = useToast()
    success('x')
    const store = useToastStore()
    const id = store.toasts[0]!.id
    dismiss(id)
    expect(store.toasts).toHaveLength(0)
  })

  it('error toast includes traceId and hint if provided', () => {
    const { error } = useToast()
    error('设备离线', { hint: '稍后再试', traceId: 'T-1' })
    const store = useToastStore()
    expect(store.toasts[0]?.traceId).toBe('T-1')
    expect(store.toasts[0]?.hint).toBe('稍后再试')
  })
})
