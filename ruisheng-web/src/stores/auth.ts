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

const AUTHORITIES: ReadonlySet<string> = new Set([
  'Administrators',
  'GroupCompany',
  'Company',
  'User',
])

function isOptionalString(value: unknown): boolean {
  return value === undefined || typeof value === 'string'
}

function isUserInfo(value: unknown): value is UserInfo {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.user_name === 'string' &&
    candidate.user_name.trim().length > 0 &&
    typeof candidate.authority === 'string' &&
    AUTHORITIES.has(candidate.authority) &&
    typeof candidate.usr_group === 'string' &&
    (candidate.control_authority === undefined ||
      (typeof candidate.control_authority === 'number' &&
        Number.isInteger(candidate.control_authority))) &&
    isOptionalString(candidate.company) &&
    isOptionalString(candidate.department)
  )
}

function decodeAccessClaims(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const payload = parts[1]
    if (!payload) return null
    const encoded = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padded = encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=')
    const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0))
    const claims: unknown = JSON.parse(new TextDecoder().decode(bytes))
    return typeof claims === 'object' && claims !== null
      ? (claims as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function matchesAccessClaims(user: UserInfo, token: string): boolean {
  const claims = decodeAccessClaims(token)
  return (
    claims !== null &&
    claims.typ === 'access' &&
    claims.sub === user.user_name &&
    claims.role === user.authority &&
    claims.usr_group === user.usr_group &&
    claims.ca === (user.control_authority ?? 0)
  )
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
    try {
      localStorage.setItem('access_token', s.access_token)
      localStorage.setItem('refresh_token', s.refresh_token)
      localStorage.setItem('user', JSON.stringify(s.user))
    } catch {}
  }

  function logout(): void {
    user.value = null
    accessToken.value = null
    refreshToken.value = null
    setAuthToken(null)
    try {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user')
    } catch {}
  }

  function hydrate(): void {
    try {
      const token = localStorage.getItem('access_token')
      const refresh = localStorage.getItem('refresh_token')
      const storedUser = localStorage.getItem('user')
      if (!token || !storedUser) {
        logout()
        return
      }
      const hydratedUser: unknown = JSON.parse(storedUser)
      if (!isUserInfo(hydratedUser) || !matchesAccessClaims(hydratedUser, token)) {
        logout()
        return
      }
      accessToken.value = token
      refreshToken.value = refresh
      user.value = hydratedUser
      setAuthToken(token)
    } catch {
      logout()
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
