<script setup lang="ts">
import { ref, computed } from 'vue'
import { RouterLink, RouterView, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAlarmsStore } from '@/stores/alarms'
import { useWsStore } from '@/stores/ws'
import { logout as apiLogout } from '@/api/auth'
import CommandPalette from '@/components/CommandPalette.vue'
import { useWsConnection } from '@/composables/useWsConnection'

const router = useRouter()
const auth = useAuthStore()
const alarms = useAlarmsStore()
const ws = useWsStore()
useWsConnection()

const sidebarOpen = ref(true)

const navItems = computed<Array<{ to: string; label: string; icon: string; badge?: number }>>(() => [
  { to: '/dashboard', label: '概览', icon: '📊' },
  { to: '/devices', label: '设备', icon: '🖥' },
  { to: '/alarms', label: '告警', icon: '🚨', badge: alarms.unackedCount },
  { to: '/reports', label: '报表', icon: '📈' },
  { to: '/waveforms', label: '波形', icon: '🌊' },
  { to: '/plans/timing', label: '定时', icon: '⏱' },
  { to: '/plans/maintenance', label: '保养', icon: '🔧' },
  { to: '/scenes', label: '组态', icon: '🗺' },
  { to: '/pay', label: '充值', icon: '💳' },
  { to: '/settings/users', label: '用户', icon: '👥' },
])

async function onLogout(): Promise<void> {
  try {
    await apiLogout()
  } catch {
    /* ignore */
  }
  auth.logout()
  await router.push('/login')
}

function toggleSidebar(): void {
  sidebarOpen.value = !sidebarOpen.value
}
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar" :class="{ collapsed: !sidebarOpen }">
      <div class="brand">
        <span class="logo">🏭</span>
        <span v-if="sidebarOpen">润盛 SCADA</span>
      </div>
      <nav>
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          active-class="active"
        >
          <span class="icon">{{ item.icon }}</span>
          <span v-if="sidebarOpen" class="label">{{ item.label }}</span>
          <span v-if="sidebarOpen && item.badge" class="badge">{{ item.badge }}</span>
        </RouterLink>
      </nav>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="toggle" aria-label="toggle sidebar" @click="toggleSidebar">☰</button>
        <div class="spacer"></div>
        <div class="ws-status" :data-state="ws.state">
          {{ ws.isHealthy ? '● 在线' : '● ' + ws.state }}
        </div>
        <div class="user">
          <span>{{ auth.user?.user_name ?? '未登录' }}</span>
          <button class="logout" @click="onLogout">登出</button>
        </div>
      </header>
      <main class="content">
        <RouterView />
      </main>
    </div>
  </div>
  <CommandPalette />
</template>

<style scoped>
.app-shell { display: flex; min-height: 100vh; }
.sidebar {
  width: 220px;
  background: #1e293b;
  color: #e2e8f0;
  transition: width 0.2s;
  display: flex;
  flex-direction: column;
}
.sidebar.collapsed { width: 56px; }
.brand {
  padding: 16px;
  font-weight: bold;
  display: flex;
  align-items: center;
  gap: 8px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  color: #cbd5e1;
  text-decoration: none;
  font-size: 14px;
}
.nav-item:hover { background: #334155; }
.nav-item.active { background: var(--color-primary); color: white; }
.icon { width: 20px; text-align: center; }
.badge {
  margin-left: auto;
  background: var(--color-error);
  color: white;
  padding: 0 6px;
  border-radius: 10px;
  font-size: 11px;
}
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar {
  height: 48px;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 12px;
  background: var(--color-bg);
}
.toggle {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
}
.spacer { flex: 1; }
.ws-status {
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 4px;
  background: var(--color-bg-alt);
}
.ws-status[data-state='open'] { color: var(--color-success); }
.ws-status[data-state='reconnecting'] { color: var(--color-warning); }
.ws-status[data-state='closed'] { color: var(--color-error); }
.user { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.logout {
  background: none;
  border: 1px solid #ccc;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.content { flex: 1; padding: 16px; overflow: auto; background: var(--color-bg-alt); }

@media (max-width: 768px) {
  .sidebar { position: fixed; z-index: 10; height: 100vh; }
  .sidebar.collapsed { width: 0; overflow: hidden; }
}
</style>
