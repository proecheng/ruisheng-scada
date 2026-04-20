import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { setAuthToken } from '@/api/client'

export type Authority = 'Administrators' | 'GroupCompany' | 'Company' | 'User'

export interface UserInfo {
  user_name: string
  authority: Authority
  usr_group: string
  control_authority?: number
  company?: string
  department?: string
}

export interface Session {
  access_token: string
  refresh_token: string
  user: UserInfo
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const accessToken = ref<string | null>(null)
  const refreshToken = ref<string | null>(null)

  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)

  function setSession(s: Session): void {
    user.value = s.user
    accessToken.value = s.access_token
    refreshToken.value = s.refresh_token
    setAuthToken(s.access_token)
    localStorage.setItem('access_token', s.access_token)
    localStorage.setItem('refresh_token', s.refresh_token)
    localStorage.setItem('user', JSON.stringify(s.user))
  }

  function logout(): void {
    user.value = null
    accessToken.value = null
    refreshToken.value = null
    setAuthToken(null)
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user')
  }

  function hydrate(): void {
    const token = localStorage.getItem('access_token')
    const refresh = localStorage.getItem('refresh_token')
    const u = localStorage.getItem('user')
    if (token && u) {
      accessToken.value = token
      refreshToken.value = refresh
      user.value = JSON.parse(u) as UserInfo
      setAuthToken(token)
    }
  }

  function hasRole(allowed: Authority[]): boolean {
    return !!user.value && allowed.includes(user.value.authority)
  }

  function hasControlBit(bit: number): boolean {
    return !!user.value && ((user.value.control_authority ?? 0) & bit) !== 0
  }

  return {
    user,
    accessToken,
    refreshToken,
    isAuthenticated,
    setSession,
    logout,
    hydrate,
    hasRole,
    hasControlBit,
  }
})
