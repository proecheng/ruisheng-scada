<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getDevice, updateDevice, type DeviceUpdatePayload } from '@/api/devices'
import { listDeviceTemplates, type DeviceTemplate } from '@/api/templates'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()

const templates = ref<DeviceTemplate[]>([])
const isLoading = ref(true)
const isSubmitting = ref(false)
const devName = ref('')
const devType = ref('')
const transportType = ref<'tcp' | 'serial'>('tcp')
const serialPort = ref('')
const devIp = ref('')
const modbusAddr = ref<string | number>('1')
const baudRate = ref<string | number>('9600')
const updateIntervalSeconds = ref<string | number>('10')
const groupCompany = ref('')
const company = ref('')
const department = ref('')
const isEnabled = ref(true)

const title = computed(() => `${props.devNumber} — 编辑设备`)

function optionalText(value: string): string | undefined {
  const trimmed = value.trim()
  return trimmed === '' ? undefined : trimmed
}

function optionalInt(value: string | number): number | undefined {
  const trimmed = String(value).trim()
  return trimmed === '' ? undefined : Number(trimmed)
}

function requiredInt(value: string | number, label: string): number {
  const parsed = Number(String(value).trim())
  if (!Number.isInteger(parsed)) throw new Error(`${label}必须是整数`)
  return parsed
}

onMounted(async () => {
  try {
    const [device, loadedTemplates] = await Promise.all([getDevice(props.devNumber), listDeviceTemplates()])
    templates.value = loadedTemplates
    devName.value = device.dev_name ?? ''
    devType.value = device.dev_type ?? ''
    transportType.value = device.transport_type ?? 'tcp'
    serialPort.value = device.serial_port ?? ''
    devIp.value = device.dev_ip ?? ''
    modbusAddr.value = device.modbus_addr ?? 1
    baudRate.value = device.baud_rate ?? ''
    updateIntervalSeconds.value = Math.round((device.update_interval_decisec ?? 100) / 10)
    groupCompany.value = device.group_company ?? ''
    company.value = device.company ?? ''
    department.value = device.department ?? ''
    isEnabled.value = device.is_enabled ?? true
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '加载设备失败')
  } finally {
    isLoading.value = false
  }
})

function buildPayload(): DeviceUpdatePayload {
  const addr = requiredInt(modbusAddr.value, 'Modbus 地址')
  if (addr < 1 || addr > 247) throw new Error('Modbus 地址范围为 1-247')
  const baud = optionalInt(baudRate.value)
  if (baud !== undefined && (!Number.isInteger(baud) || baud <= 0)) {
    throw new Error('波特率必须是正整数')
  }
  const intervalSeconds = optionalInt(updateIntervalSeconds.value)
  if (
    intervalSeconds !== undefined &&
    (!Number.isInteger(intervalSeconds) || intervalSeconds < 1 || intervalSeconds > 100)
  ) {
    throw new Error('轮询间隔范围为 1-100 秒')
  }
  const port = optionalText(serialPort.value)
  if (transportType.value === 'serial' && port === undefined) {
    throw new Error('串口设备必须填写串口号')
  }
  return {
    dev_name: optionalText(devName.value),
    dev_type: optionalText(devType.value),
    transport_type: transportType.value,
    serial_port: transportType.value === 'serial' ? port : null,
    dev_ip: transportType.value === 'tcp' ? (optionalText(devIp.value) ?? null) : null,
    modbus_addr: addr,
    baud_rate: baud,
    update_interval_decisec: intervalSeconds === undefined ? undefined : intervalSeconds * 10,
    is_enabled: isEnabled.value,
    group_company: optionalText(groupCompany.value),
    company: optionalText(company.value),
    department: optionalText(department.value),
  }
}

async function submit(): Promise<void> {
  if (isSubmitting.value) return
  isSubmitting.value = true
  try {
    await updateDevice(props.devNumber, buildPayload())
    toast.success('已保存')
    await router.push(`/devices/${encodeURIComponent(props.devNumber)}`)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '保存设备失败')
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <section class="device-edit">
    <header class="page-header">
      <button class="back" type="button" @click="router.back()">返回</button>
      <h2>{{ title }}</h2>
    </header>

    <LoadingSkeleton v-if="isLoading" :lines="6" />
    <form v-else class="device-form" @submit.prevent="submit">
      <fieldset>
        <legend>基础信息</legend>
        <label>
          启用设备
          <select v-model="isEnabled">
            <option :value="true">启用</option>
            <option :value="false">停用</option>
          </select>
        </label>
        <label>
          设备名称
          <input v-model="devName" type="text" autocomplete="off" />
        </label>
        <label>
          设备类型
          <input v-model="devType" type="text" list="device-type-options-edit" autocomplete="off" />
          <datalist id="device-type-options-edit">
            <option v-for="t in templates" :key="t.id" :value="t.dev_type ?? t.name" />
          </datalist>
        </label>
      </fieldset>

      <fieldset>
        <legend>通信参数</legend>
        <label>
          通信方式
          <select v-model="transportType">
            <option value="tcp">TCP</option>
            <option value="serial">串口</option>
          </select>
        </label>
        <label v-if="transportType === 'serial'">
          串口号
          <input v-model="serialPort" type="text" required autocomplete="off" />
        </label>
        <label v-if="transportType === 'tcp'">
          设备来源 IP
          <input v-model="devIp" type="text" autocomplete="off" placeholder="留空表示不限制" />
        </label>
        <label>
          Modbus 地址
          <input v-model="modbusAddr" type="number" min="1" max="247" required />
        </label>
        <label>
          波特率
          <input v-model="baudRate" type="number" min="1" step="1" />
        </label>
        <label>
          轮询间隔（秒）
          <input v-model="updateIntervalSeconds" type="number" min="1" max="100" step="1" />
        </label>
      </fieldset>

      <fieldset>
        <legend>归属信息</legend>
        <label>
          集团
          <input v-model="groupCompany" type="text" autocomplete="off" />
        </label>
        <label>
          公司
          <input v-model="company" type="text" autocomplete="off" />
        </label>
        <label>
          部门
          <input v-model="department" type="text" autocomplete="off" />
        </label>
      </fieldset>

      <footer class="actions">
        <button type="button" class="secondary" @click="router.back()">取消</button>
        <button type="submit" :disabled="isSubmitting">{{ isSubmitting ? '提交中...' : '保存' }}</button>
      </footer>
    </form>
  </section>
</template>

<style scoped>
.device-edit { background: #fff; padding: 16px; border-radius: 6px; }
.page-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
h2 { font-size: 18px; }
.device-form { display: grid; gap: 14px; max-width: 920px; }
fieldset { border: 1px solid #e5e7eb; border-radius: 6px; padding: 14px; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
legend { padding: 0 6px; font-size: 13px; font-weight: 600; color: var(--color-text-secondary); }
label { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--color-text-secondary); }
input, select { width: 100%; box-sizing: border-box; padding: 7px 9px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; color: var(--color-text); background: #fff; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { padding: 7px 16px; border-radius: 4px; border: none; background: var(--color-primary); color: #fff; cursor: pointer; }
.actions button:disabled { opacity: 0.55; cursor: not-allowed; }
.actions .secondary { background: #fff; color: var(--color-text); border: 1px solid #ccc; }
</style>
