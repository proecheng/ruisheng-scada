import { ref, computed } from 'vue'

export type AsyncState = 'idle' | 'pending' | 'success' | 'error'

export function useAsync<Args extends unknown[], R>(fn: (...args: Args) => Promise<R>) {
  const state = ref<AsyncState>('idle')
  const data = ref<R | null>(null)
  const error = ref<Error | null>(null)

  const isPending = computed(() => state.value === 'pending')
  const isError = computed(() => state.value === 'error')
  const isSuccess = computed(() => state.value === 'success')

  async function run(...args: Args): Promise<R> {
    state.value = 'pending'
    error.value = null
    try {
      const result = await fn(...args)
      data.value = result as never
      state.value = 'success'
      return result
    } catch (e) {
      error.value = e instanceof Error ? e : new Error(String(e))
      state.value = 'error'
      throw e
    }
  }

  function reset(): void {
    state.value = 'idle'
    data.value = null
    error.value = null
  }

  return { state, data, error, isPending, isError, isSuccess, run, reset }
}
