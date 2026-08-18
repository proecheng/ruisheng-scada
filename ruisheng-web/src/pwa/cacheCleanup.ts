const LEGACY_AUTHENTICATED_CACHE_NAMES = ['api-cache']
const LEGACY_RELOAD_KEY = 'ruisheng:legacy-pwa-retired'
const LEGACY_SERVICE_WORKER_PATH = '/sw.js'

function registrationScriptPath(registration: ServiceWorkerRegistration): string | null {
  const scriptUrl =
    registration.active?.scriptURL ??
    registration.waiting?.scriptURL ??
    registration.installing?.scriptURL
  return scriptUrl ? new URL(scriptUrl).pathname : null
}

export async function retireLegacyAuthenticatedCaches(): Promise<boolean> {
  if (!('caches' in globalThis)) return false
  const legacyCaches = await Promise.all(
    LEGACY_AUTHENTICATED_CACHE_NAMES.map((cacheName) => globalThis.caches.has(cacheName)),
  )
  const hasLegacyCache = legacyCaches.some(Boolean)
  const registrations =
    'serviceWorker' in navigator ? await navigator.serviceWorker.getRegistrations() : []
  const legacyRegistrations = registrations.filter(
    (registration) => registrationScriptPath(registration) === LEGACY_SERVICE_WORKER_PATH,
  )
  await Promise.all(legacyRegistrations.map((registration) => registration.unregister()))
  await Promise.all(
    LEGACY_AUTHENTICATED_CACHE_NAMES.map((cacheName) => globalThis.caches.delete(cacheName)),
  )
  if (
    (hasLegacyCache || legacyRegistrations.length > 0) &&
    navigator.serviceWorker?.controller &&
    sessionStorage.getItem(LEGACY_RELOAD_KEY) !== '1'
  ) {
    sessionStorage.setItem(LEGACY_RELOAD_KEY, '1')
    location.reload()
    return true
  }
  sessionStorage.removeItem(LEGACY_RELOAD_KEY)
  return false
}
