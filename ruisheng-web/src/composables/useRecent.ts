import { ref } from 'vue'

export function useRecent<T>(key: string, maxItems = 5) {
  const storageKey = `recent:${key}`
  const items = ref<T[]>([])

  const raw = localStorage.getItem(storageKey)
  if (raw) {
    try {
      items.value = JSON.parse(raw) as T[]
    } catch {
      items.value = []
    }
  }

  function save(): void {
    localStorage.setItem(storageKey, JSON.stringify(items.value))
  }

  function push(item: T): void {
    const i = items.value.findIndex((x) => JSON.stringify(x) === JSON.stringify(item))
    if (i >= 0) items.value.splice(i, 1)
    ;(items.value as T[]).unshift(item)
    if (items.value.length > maxItems) items.value.length = maxItems
    save()
  }

  function clear(): void {
    items.value = []
    save()
  }

  return { items, push, clear }
}
