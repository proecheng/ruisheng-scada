import { afterEach, describe, expect, it, vi } from 'vitest'
import { retireLegacyAuthenticatedCaches } from '@/pwa/cacheCleanup'

describe('legacy authenticated cache cleanup', () => {
  afterEach(() => {
    sessionStorage.clear()
    vi.unstubAllGlobals()
  })

  it('removes API responses cached by older service workers', async () => {
    const removeCache = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('caches', { has: vi.fn().mockResolvedValue(true), delete: removeCache })

    await retireLegacyAuthenticatedCaches()

    expect(removeCache).toHaveBeenCalledWith('api-cache')
  })

  it('unregisters a legacy worker before deleting its authenticated cache', async () => {
    const events: string[] = []
    const unregister = vi.fn().mockImplementation(async () => {
      events.push('unregister')
      return true
    })
    const removeCache = vi.fn().mockImplementation(async () => {
      events.push('delete')
      return true
    })
    vi.stubGlobal('caches', { has: vi.fn().mockResolvedValue(true), delete: removeCache })
    vi.stubGlobal('navigator', {
      serviceWorker: {
        controller: null,
        getRegistrations: vi.fn().mockResolvedValue([
          { active: { scriptURL: 'https://example.test/sw.js' }, unregister },
        ]),
      },
    })

    const reloading = await retireLegacyAuthenticatedCaches()

    expect(events).toEqual(['unregister', 'delete'])
    expect(reloading).toBe(false)
  })

  it('keeps the replacement worker registered on later starts', async () => {
    const unregister = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('caches', {
      has: vi.fn().mockResolvedValue(false),
      delete: vi.fn().mockResolvedValue(false),
    })
    vi.stubGlobal('navigator', {
      serviceWorker: {
        controller: {},
        getRegistrations: vi.fn().mockResolvedValue([
          { active: { scriptURL: 'https://example.test/sw-safe-v2.js' }, unregister },
        ]),
      },
    })

    const reloading = await retireLegacyAuthenticatedCaches()

    expect(unregister).not.toHaveBeenCalled()
    expect(reloading).toBe(false)
  })
})
