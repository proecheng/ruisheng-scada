<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { getVersion, getReady, type MetaVersion, type ReadyCheck } from '@/api/meta'
import RequestLogPanel from '@/debug/RequestLogPanel.vue'
import WsStatePanel from '@/debug/WsStatePanel.vue'

const auth = useAuthStore()
const version = ref<MetaVersion | null>(null)
const ready = ref<ReadyCheck | null>(null)
const clientBuild = import.meta.env.VITE_BUILD_HASH

const clientNow = ref(Date.now())

onMounted(async () => {
  try {
    version.value = await getVersion()
  } catch {
    /* ignore */
  }
  try {
    ready.value = await getReady()
  } catch {
    /* ignore */
  }
  setInterval(() => (clientNow.value = Date.now()), 1000)
})

function copy(s: string): void {
  void navigator.clipboard?.writeText(s)
}
</script>

<template>
  <section class="diag">
    <h2>诊断页 /__diag</h2>

    <div class="grid">
      <section class="card">
        <h3>当前会话</h3>
        <dl>
          <dt>用户名</dt><dd>{{ auth.user?.user_name ?? '—' }}</dd>
          <dt>角色</dt><dd>{{ auth.user?.authority ?? '—' }}</dd>
          <dt>租户</dt><dd>{{ auth.user?.usr_group ?? '—' }}</dd>
          <dt>Control Authority</dt><dd>0x{{ auth.user?.control_authority?.toString(16) ?? '0' }}</dd>
        </dl>
      </section>

      <section class="card">
        <h3>版本</h3>
        <dl>
          <dt>前端</dt><dd>{{ clientBuild }}<button class="mini" @click="copy(clientBuild)">复制</button></dd>
          <dt>API</dt><dd>{{ version?.api_version ?? '—' }}</dd>
          <dt>API Build</dt><dd>{{ version?.build_hash ?? '—' }}</dd>
          <dt>DB Schema</dt><dd>{{ version?.db_schema_version ?? '—' }}</dd>
        </dl>
      </section>

      <section class="card">
        <h3>健康检查</h3>
        <div v-if="ready">
          <p :class="{ ok: ready.ok, fail: !ready.ok }">
            {{ ready.ok ? '✓ 全部组件健康' : '✗ 存在降级' }}
          </p>
          <ul>
            <li v-for="(c, k) in ready.components" :key="k" :class="{ ok: c.ok, fail: !c.ok }">
              {{ k }}: {{ c.ok ? 'ok' : (c.detail ?? 'fail') }}
            </li>
          </ul>
        </div>
        <p v-else>加载中…</p>
      </section>

      <section class="card">
        <h3>时钟</h3>
        <p>客户端：{{ new Date(clientNow).toLocaleString() }}</p>
      </section>
    </div>

    <section class="card">
      <h3>最近 50 次 API/错误</h3>
      <RequestLogPanel />
    </section>

    <section class="card">
      <h3>WebSocket</h3>
      <WsStatePanel />
    </section>
  </section>
</template>

<style scoped>
.diag { background: var(--color-bg-alt); padding: 0; }
h2 { font-size: 18px; margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-bottom: 12px; }
.card { background: #fff; padding: 14px; border-radius: 6px; margin-bottom: 12px; }
.card h3 { font-size: 14px; margin-bottom: 10px; }
dl { display: grid; grid-template-columns: 110px 1fr; gap: 4px; font-size: 13px; }
dt { color: var(--color-text-secondary); }
dd { display: flex; align-items: center; gap: 6px; }
.mini { font-size: 10px; padding: 1px 6px; border: 1px solid #ccc; border-radius: 3px; cursor: pointer; background: #fff; }
.ok { color: var(--color-success); }
.fail { color: var(--color-error); }
ul { list-style: none; font-size: 13px; }
</style>
