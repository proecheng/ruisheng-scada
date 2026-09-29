<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useToast } from '@/composables/useToast'
import DoControlPanel from '@/components/DoControlPanel.vue'
import { getDevice } from '@/api/devices'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()
const transportType = ref<string | null>(null)

onMounted(async () => {
  try {
    const device = await getDevice(props.devNumber)
    transportType.value = device.transport_type ?? 'tcp'
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载设备失败')
  }
})
</script>

<template>
  <section class="device-control">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 远程控制</h2>
    </header>

    <DoControlPanel v-if="transportType === 'serial'" :key="devNumber" :dev-number="devNumber" />
    <p v-else-if="transportType" class="notice">
      通用寄存器写命令不会被网关执行，因此这里不再下发。TCP 设备的启动、停止和复位需要单独的执行链路；串口设备使用逐路闭合/断开。
    </p>
  </section>
</template>

<style scoped>
.device-control { background: #fff; padding: 16px; border-radius: 6px; max-width: 560px; }
.notice { margin: 0; padding: 12px; background: #fff7ed; border: 1px solid #fdba74; border-radius: 4px; font-size: 14px; line-height: 1.5; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; margin-right: 8px; }
header { margin-bottom: 16px; }
h2 { display: inline; font-size: 18px; }
</style>
