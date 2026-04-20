import type { Directive, DirectiveBinding } from 'vue'
import { useAuthStore, type Authority } from '@/stores/auth'

function check(value: unknown): boolean {
  const auth = useAuthStore()
  if (!auth.user) return false
  const allowed: Authority[] = Array.isArray(value) ? (value as Authority[]) : [value as Authority]
  return allowed.includes(auth.user.authority)
}

function apply(el: HTMLElement, binding: DirectiveBinding): void {
  if (!check(binding.value)) {
    el.parentNode?.removeChild(el)
  }
}

export const permissionDirective: Directive = {
  mounted: apply,
  updated: apply,
}
