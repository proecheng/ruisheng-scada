<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createDevice, type DeviceCreatePayload, type DeviceReadProfile } from '@/api/devices'
import { listDeviceTemplates, type DeviceTemplate } from '@/api/templates'
import { createPoint } from '@/api/points'
import { incompatibleTemplatePoint } from '@/utils/templatePoints'
import { useDevicesStore } from '@/stores/devices'
import { useToast } from '@/composables/useToast'

const router = useRouter()
const devicesStore = useDevicesStore()
const toast = useToast()

const devNumber = ref('')
const serialNumber = ref('')
const devName = ref('')
const devType = ref('')
const templates = ref<DeviceTemplate[]>([])
const selectedTemplateId = ref<string>('')
const transportType = ref<'tcp' | 'serial'>('tcp')
const readProfile = ref<DeviceReadProfile>('point_groups')
const serialPort = ref('')
const devIp = ref('')
const modbusAddr = ref<string | number>('1')
const baudRate = ref<string | number>('9600')
const updateIntervalSeconds = ref<string | number>('10')
const iccid = ref('')
const groupCompany = ref('')
const company = ref('')
const department = ref('')
const isSubmitting = ref(false)

const canSubmit = computed(() => devNumber.value.trim() !== '' && serialNumber.value.trim() !== '')
const selectedTemplate = computed(() =>
  templates.value.find((t) => String(t.id) === selectedTemplateId.value),
)

onMounted(async () => {
  try {
    templates.value = await listDeviceTemplates()
  } catch {
    templates.value = []
  }
})

function applyTemplate(): void {
  const t = selectedTemplate.value
  if (!t) return
  devType.value = t.dev_type ?? ''
  if (!devName.value) devName.value = t.name
}

function optionalText(value: string): string | undefined {
  const trimmed = value.trim()
  return trimmed === '' ? undefined : trimmed
}

function optionalInt(value: string | number): number | undefined {
  const trimmed = String(value).trim()
  return trimmed === '' ? undefined : Number(trimmed)
}

function parseRequiredInt(value: string | number, label: string): number {
  const parsed = Number(String(value).trim())
  if (!Number.isInteger(parsed)) {
    throw new Error(`${label}必须是整数`)
  }
  return parsed
}

function buildPayload(): DeviceCreatePayload {
  const addr = parseRequiredInt(modbusAddr.value, 'Modbus 地址')
  if (addr < 1 || addr > 247) {
    throw new Error('Modbus 地址范围为 1-247')
  }

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
    dev_number: devNumber.value.trim(),
    dev_ser_number: serialNumber.value.trim(),
    transport_type: transportType.value,
    read_profile: transportType.value === 'serial' ? readProfile.value : 'point_groups',
    serial_port: transportType.value === 'serial' ? port : undefined,
    dev_ip: transportType.value === 'tcp' ? optionalText(devIp.value) : undefined,
    modbus_addr: addr,
    iccid: optionalText(iccid.value),
    dev_name: optionalText(devName.value),
    dev_type: optionalText(devType.value),
    baud_rate: baud,
    update_interval_decisec: intervalSeconds === undefined ? undefined : intervalSeconds * 10,
    group_company: optionalText(groupCompany.value),
    company: optionalText(company.value),
    department: optionalText(department.value),
  }
}

async function submit(): Promise<void> {
  if (!canSubmit.value || isSubmitting.value) return
  isSubmitting.value = true
  let createdNumber = ''
  try {
    const payload = buildPayload()
    const templatePoints = selectedTemplate.value?.payload.points ?? []
    const incompatible = incompatibleTemplatePoint(payload.read_profile ?? 'point_groups', templatePoints)
    if (incompatible) throw new Error(incompatible)
    const created = await createDevice(payload)
    createdNumber = created.dev_number
    if (templatePoints.length > 0) {
      await Promise.all(templatePoints.map((p) => createPoint(created.dev_number, p)))
    }
    devicesStore.setList([
      {
        dev_number: created.dev_number,
        dev_name: created.dev_name,
        state: created.state,
        company: created.company,
        department: created.department,
      },
      ...devicesStore.list,
    ])
    toast.success(`已添加设备 ${created.dev_number}`)
    await router.push(`/devices/${encodeURIComponent(created.dev_number)}`)
  } catch (e) {
    const message = e instanceof Error ? e.message : '添加设备失败'
    toast.error(
      createdNumber
        ? `设备 ${createdNumber} 已创建，但模板点位未全部写入：${message}。请到该设备的点位配置补齐，不要用同一设备号重复添加。`
        : message,
    )
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <section class="device-create">
    <header class="page-header">
      <button class="back" type="button" @click="router.push('/devices')">返回</button>
      <h2>添加设备</h2>
    </header>

    <form class="device-form" @submit.prevent="submit">
      <fieldset>
        <legend>基础信息</legend>
        <label>
          设备号
          <input v-model="devNumber" type="text" required autocomplete="off" placeholder="DEV001" />
        </label>
        <label>
          设备序列号
          <input v-model="serialNumber" type="text" required autocomplete="off" placeholder="SN-DEV001" />
        </label>
        <label>
          设备名称
          <input v-model="devName" type="text" autocomplete="off" placeholder="1号泵站" />
        </label>
        <label>
          设备模板
          <select v-model="selectedTemplateId" @change="applyTemplate">
            <option value="">不使用模板</option>
            <option v-for="t in templates" :key="t.id" :value="String(t.id)">
              {{ t.name }}{{ t.dev_type ? `（${t.dev_type}）` : '' }}
            </option>
          </select>
        </label>
        <label>
          设备类型
          <input v-model="devType" type="text" list="device-type-options" autocomplete="off" placeholder="pump" />
          <datalist id="device-type-options">
            <option v-for="t in templates" :key="t.id" :value="t.dev_type ?? t.name" />
          </datalist>
        </label>
        <label>
          ICCID
          <input v-model="iccid" type="text" autocomplete="off" placeholder="8986..." />
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
          <input v-model="serialPort" type="text" required autocomplete="off" placeholder="COM3 或 /dev/ttyUSB0" />
        </label>
        <label v-if="transportType === 'serial'">
          读取方案
          <select v-model="readProfile">
            <option value="point_groups">按点位分组</option>
            <option value="zero_origin_38">自研设备38寄存器</option>
          </select>
        </label>
        <label v-if="transportType === 'tcp'">
          设备来源 IP
          <input v-model="devIp" type="text" autocomplete="off" placeholder="留空表示不限制，例如 192.168.1.20" />
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
        <button type="button" class="secondary" @click="router.push('/devices')">取消</button>
        <button type="submit" :disabled="!canSubmit || isSubmitting">
          {{ isSubmitting ? '提交中…' : '保存' }}
        </button>
      </footer>
    </form>
  </section>
</template>

<style scoped>
.device-create { background: #fff; padding: 16px; border-radius: 6px; }
.page-header { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
h2 { font-size: 18px; }
.device-form { display: grid; gap: 14px; max-width: 920px; }
fieldset { min-width: 0; border: 1px solid #e5e7eb; border-radius: 6px; padding: 14px; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
legend { padding: 0 6px; font-size: 13px; font-weight: 600; color: var(--color-text-secondary); }
label { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--color-text-secondary); }
input, select { width: 100%; box-sizing: border-box; padding: 7px 9px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; color: var(--color-text); background: #fff; }
input:focus, select:focus { outline: 2px solid rgba(22, 119, 255, 0.18); border-color: var(--color-primary); }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.actions button { padding: 7px 16px; border-radius: 4px; border: none; background: var(--color-primary); color: #fff; cursor: pointer; }
.actions button:disabled { opacity: 0.55; cursor: not-allowed; }
.actions .secondary { background: #fff; color: var(--color-text); border: 1px solid #ccc; }
@media (max-width: 640px) {
  .device-create { padding: 12px; }
  fieldset { grid-template-columns: 1fr; }
  .actions { justify-content: stretch; }
  .actions button { flex: 1; }
}
</style>
