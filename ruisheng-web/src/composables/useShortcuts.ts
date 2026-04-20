import { onMounted, onUnmounted } from 'vue'

export interface Shortcut {
  key: string
  ctrl?: boolean
  meta?: boolean
  shift?: boolean
  alt?: boolean
  handler: (e: KeyboardEvent) => void
  preventDefault?: boolean
}

export function useShortcuts(shortcuts: Shortcut[]): void {
  function onKey(e: KeyboardEvent): void {
    for (const s of shortcuts) {
      if (e.key.toLowerCase() !== s.key.toLowerCase()) continue
      if (!!s.ctrl !== e.ctrlKey) continue
      if (!!s.meta !== e.metaKey) continue
      if (!!s.shift !== e.shiftKey) continue
      if (!!s.alt !== e.altKey) continue
      if (s.preventDefault !== false) e.preventDefault()
      s.handler(e)
      return
    }
  }
  onMounted(() => window.addEventListener('keydown', onKey))
  onUnmounted(() => window.removeEventListener('keydown', onKey))
}
