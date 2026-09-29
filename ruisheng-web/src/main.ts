import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VueKonva from 'vue-konva'
import App from './App.vue'
import { retireLegacyAuthenticatedCaches } from '@/pwa/cacheCleanup'
import router from './router'
import { i18n } from './i18n'
import { permissionDirective } from './directives/v-permission'
import { useAuthStore } from './stores/auth'
import { installSessionRenewal } from './auth/sessionRenewal'
import './styles/main.css'

async function bootstrap(): Promise<void> {
  if (await retireLegacyAuthenticatedCaches()) return

  const app = createApp(App)
  const pinia = createPinia()
  app.use(pinia)
  app.use(router)
  app.use(i18n)
  app.use(VueKonva)
  app.directive('permission', permissionDirective)

  const auth = useAuthStore()
  auth.hydrate()
  window.addEventListener('ruisheng:auth-expired', () => {
    auth.logout()
    if (router.currentRoute.value.name !== 'login') {
      void router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
    }
  })
  installSessionRenewal(auth)

  app.mount('#app')
  if (import.meta.env.PROD && 'serviceWorker' in navigator) {
    await navigator.serviceWorker.register('/sw-safe-v2.js')
  }
}

void bootstrap()
