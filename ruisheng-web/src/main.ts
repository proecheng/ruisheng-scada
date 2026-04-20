import { createApp } from 'vue'
import { createPinia } from 'pinia'
import VueKonva from 'vue-konva'
import App from './App.vue'
import router from './router'
import { i18n } from './i18n'
import { permissionDirective } from './directives/v-permission'
import { useAuthStore } from './stores/auth'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(i18n)
app.use(VueKonva)
app.directive('permission', permissionDirective)

const auth = useAuthStore()
auth.hydrate()

app.mount('#app')
