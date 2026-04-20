import { defineStore } from 'pinia'
import { ref } from 'vue'
import { generateUlid } from '@/utils/ulid'

export type ToastType = 'success' | 'info' | 'warning' | 'error'

export interface Toast {
  id: string
  type: ToastType
  message: string
  hint?: string
  traceId?: string
  at: number
}

export const useToastStore = defineStore('toast', () => {
  const toasts = ref<Toast[]>([])

  function add(t: Omit<Toast, 'id' | 'at'>): string {
    const id = generateUlid()
    toasts.value.push({ ...t, id, at: Date.now() })
    return id
  }
  function remove(id: string): void {
    const i = toasts.value.findIndex((x) => x.id === id)
    if (i >= 0) toasts.value.splice(i, 1)
  }
  return { toasts, add, remove }
})

export function useToast() {
  const store = useToastStore()

  function push(
    type: ToastType,
    message: string,
    extras?: { hint?: string; traceId?: string; timeoutMs?: number },
  ): string {
    const id = store.add({ type, message, hint: extras?.hint, traceId: extras?.traceId })
    const dur = extras?.timeoutMs ?? (type === 'success' || type === 'info' ? 2000 : 0)
    if (dur > 0) setTimeout(() => store.remove(id), dur)
    return id
  }

  return {
    success: (m: string, e?: { timeoutMs?: number }) => push('success', m, e),
    info: (m: string, e?: { timeoutMs?: number }) => push('info', m, e),
    warning: (m: string, e?: { hint?: string; traceId?: string }) => push('warning', m, e),
    error: (m: string, e?: { hint?: string; traceId?: string }) => push('error', m, e),
    dismiss: (id: string) => store.remove(id),
  }
}
