import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { permissionDirective } from '@/directives/v-permission'
import { useAuthStore } from '@/stores/auth'
import { defineComponent } from 'vue'

function mountWithDirective(authority: string) {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.setSession({
    access_token: 't',
    refresh_token: 'r',
    user: { user_name: 'u', authority: authority as never, usr_group: 'g' },
  })
  const Comp = defineComponent({
    template: `<div>
      <button v-permission="'Administrators'" class="admin">admin</button>
      <button v-permission="['Company','GroupCompany']" class="mgr">mgr</button>
    </div>`,
  })
  return mount(Comp, { global: { directives: { permission: permissionDirective } } })
}

describe('v-permission', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('hides button when authority does not match (User)', () => {
    const w = mountWithDirective('User')
    expect(w.find('.admin').exists()).toBe(false)
    expect(w.find('.mgr').exists()).toBe(false)
  })

  it('shows button when authority matches (Administrators)', () => {
    const w = mountWithDirective('Administrators')
    expect(w.find('.admin').exists()).toBe(true)
  })

  it('shows button when authority is in allowed array (Company)', () => {
    const w = mountWithDirective('Company')
    expect(w.find('.mgr').exists()).toBe(true)
    expect(w.find('.admin').exists()).toBe(false)
  })
})
