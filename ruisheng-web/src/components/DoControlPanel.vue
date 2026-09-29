<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getDoInfo, getDoResult, setDoLevels, type DoInfo, type DoResult } from '@/api/doControl'
import { otpSend } from '@/api/auth'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import type { RealtimePoint } from '@/api/devices'

const props = withDefaults(defineProps<{
  devNumber: string
  canControl?: boolean
  showInputs?: boolean
  inputSample?: RealtimePoint | null
  outputSample?: RealtimePoint | null
  staleAfterMs?: number
}>(), { canControl: true, showInputs: false, inputSample: null, outputSample: null, staleAfterMs: 15000 })
const emit = defineEmits<{ configurationChanged: [] }>()
const info = ref<DoInfo | null>(null)
const levels = ref<Record<number, string>>({ 1: 'keep', 2: 'keep' })
const highRisk = ref(false)
const otp = ref('')
const busy = ref(false)
const error = ref('')
const refreshError = ref('')
const command = ref<string | null>(null)
const result = ref<DoResult | null>(null)
const showConfirm = ref(false)
const confirmed = ref<{ channels: { number: number; high: boolean }[]; version: number; highRisk: boolean; otp: string } | null>(null)
const now = ref(Date.now())
const selected = computed(() => (info.value?.channels ?? [])
  .filter(n => levels.value[n] !== 'keep')
  .map(number => ({ number, high: levels.value[number] === 'high' })))
const confirmation = computed(() => (confirmed.value?.channels ?? []).map(c => `开关${c.number}（DO${c.number}）→ ${c.high ? '闭合（1）' : '断开（0）'}`).join('；'))
const blocked = computed(() => !props.canControl || !info.value?.supported || !info.value.enabled || busy.value || !!command.value || showConfirm.value)
const output = computed(() => {
  const polled = props.outputSample
  const queried = info.value?.sample
  if (!queried || (polled && Date.parse(polled.ts) > Date.parse(queried.recorded_at))) return polled
  return { value: queried.org_value, ts: queried.recorded_at }
})
function channelState(sample: { value: number | null; ts: string } | null | undefined, n: number, isOutput: boolean) {
  if (!sample || sample.value === null) return { label: '暂无数据', value: '—', stale: true, high: undefined }
  if (!Number.isInteger(sample.value) || sample.value < 0 || sample.value > 3) return { label: '回读值异常', value: '—', stale: true, high: undefined }
  const age = now.value - Date.parse(sample.ts)
  const stale = !Number.isFinite(age) || age < 0 || age >= props.staleAfterMs
  const high = !!(sample.value & (1 << (n - 1)))
  const label = isOutput ? (high ? '已闭合' : '已断开') : (high ? '高电平' : '低电平')
  return { label: `${stale ? '上次：' : ''}${label}`, value: high ? '1' : '0', stale, high: stale ? undefined : high }
}
const inputs = computed(() => [1, 2].map(number => ({ number, ...channelState(props.inputSample, number, false) })))
const outputs = computed(() => [1, 2].map(number => ({ number, ...channelState(output.value, number, true) })))
const status = computed(() => {
  if (busy.value) return '正在提交'
  if (command.value) return '已受理，等待逐路回读确认'
  if (result.value?.result === 'success') return '执行成功，设备回读已确认'
  if (result.value?.result === 'timeout') {
    const execution = result.value.execution
    const steps = execution?.channels
    if (execution?.reason === 'response_timeout_output_may_have_changed' && steps?.length === 0) {
      return '操作前状态读取超时，本次未下发控制指令'
    }
    const last = steps?.[steps.length - 1]
    if (last?.phase === 'sending') return `开关${last.channel}写指令应答超时，输出状态未确认；未自动重发`
    if (last?.phase === 'acknowledged') return `开关${last.channel}已收到写应答，但状态回读超时；未自动重发`
    return '响应超时，输出状态未确认；未自动重发'
  }
  if (result.value?.result === 'failed') return '控制未完成，请核对各路结果和当前回读'
  if (result.value?.result === 'cancelled') return '命令已取消'
  return ''
})
let active = true
let timer: ReturnType<typeof setTimeout> | undefined
let ageTimer: ReturnType<typeof setInterval> | undefined
async function refresh(): Promise<void> {
  try {
    if (!props.canControl) return
    const latest = await getDoInfo(props.devNumber)
    if (!active) return
    const changed = info.value && info.value.config_version !== latest.config_version
    info.value = latest
    now.value = Date.now()
    refreshError.value = ''
    if (changed) emit('configurationChanged')
    if (command.value) {
      const update = await getDoResult(props.devNumber, command.value)
      if (!active) return
      result.value = update
      if (update.result !== 'pending') command.value = null
    }
  } catch (e) {
    if (active) refreshError.value = e instanceof Error ? e.message : '状态读取失败'
  } finally {
    if (active) timer = setTimeout(refresh, 1500)
  }
}
function prepare(channels = selected.value): void {
  if (blocked.value || !info.value || !channels.length) return
  if (highRisk.value && !otp.value) { error.value = '高危操作需要 OTP 验证码'; return }
  confirmed.value = { channels: channels.map(c => ({ ...c })), version: info.value.config_version, highRisk: highRisk.value, otp: otp.value }
  showConfirm.value = true
}
async function submit(): Promise<void> {
  const request = confirmed.value
  if (!request || !props.canControl || busy.value || command.value || !info.value?.supported || !info.value.enabled) return
  if (request.version !== info.value.config_version) { error.value = '设备配置已改变，请重新确认'; return }
  busy.value = true; error.value = ''; result.value = null
  try {
    const ack = await setDoLevels(props.devNumber, request.channels, request.version, request.highRisk, request.otp)
    if (!active) return
    command.value = ack.cmd_id
    levels.value = { 1: 'keep', 2: 'keep' }
  } catch (e) {
    if (active) error.value = e instanceof Error ? e.message : '提交失败，请先核对输出状态'
  } finally { busy.value = false }
}
async function requestOtp(): Promise<void> {
  try { await otpSend({ channel: 'sms', action: 'control' }) }
  catch (e) { error.value = e instanceof Error ? e.message : '验证码发送失败' }
}
onMounted(() => { void refresh(); ageTimer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { active = false; if (timer) clearTimeout(timer); if (ageTimer) clearInterval(ageTimer) })
</script>

<template>
  <section class="do-panel" aria-label="输入输出状态与控制">
    <div class="panel-heading">
      <h3>{{ showInputs ? '输入 / 输出' : '开关输出控制' }}</h3>
      <p>状态来自设备回读；下发后等待执行结果。</p>
    </div>
    <div class="digital-grid" :class="{ 'with-inputs': showInputs }">
      <template v-if="showInputs">
        <article v-for="channel in inputs" :key="`di-${channel.number}`" class="digital-channel input-channel" :data-stale="channel.stale" :data-high="channel.high" :aria-label="`DI${channel.number}输入状态`">
          <div class="channel-heading"><h4>DI{{ channel.number }}</h4><span>输入</span></div>
          <p class="channel-state"><span class="state-dot" aria-hidden="true"></span>{{ channel.label }} <b>{{ channel.value }}</b></p>
          <small v-if="channel.stale">数据未更新，等待设备新采样</small>
          <small v-else>设备当前输入电平</small>
        </article>
      </template>
      <article v-for="channel in outputs" :key="`do-${channel.number}`" class="digital-channel do-channel" :data-stale="channel.stale" :data-high="channel.high" :aria-label="`开关${channel.number}输出控制`">
        <div class="channel-heading"><h4>开关{{ channel.number }}</h4><span>DO{{ channel.number }} · 输出</span></div>
        <p class="channel-state"><span class="state-dot" aria-hidden="true"></span>{{ channel.label }} <b>{{ channel.value }}</b></p>
        <small v-if="channel.stale">回读未更新，当前状态尚未确认</small>
        <small v-else>设备当前输出回读</small>
        <div class="channel-actions">
          <button type="button" class="close-output" :disabled="blocked" :aria-label="`闭合开关${channel.number}`" @click="prepare([{ number: channel.number, high: true }])">闭合</button>
          <button type="button" :disabled="blocked" :aria-label="`断开开关${channel.number}`" @click="prepare([{ number: channel.number, high: false }])">断开</button>
        </div>
      </article>
    </div>
    <p v-if="!canControl" class="hint">当前账号无输出控制权限，可查看设备状态。</p>
    <p v-else-if="!info && !refreshError" class="hint" role="status">正在读取控制配置…</p>
    <p v-else-if="info && !info.supported" class="hint">此设备尚未配置 DO 控制协议。</p>
    <template v-if="canControl && info?.supported">
      <details class="multi-channel">
        <summary>同时设置两路</summary>
        <p>未选通道保持不变；多路依次执行，某路失败后停止。</p>
        <div class="multi-options">
          <label v-for="channel in info.channels" :key="channel">开关{{ channel }}
            <select v-model="levels[channel]" :disabled="blocked" :aria-label="`开关${channel}目标状态`">
              <option value="keep">保持不变</option><option value="high">闭合（1）</option><option value="low">断开（0）</option>
            </select>
          </label>
          <button type="button" class="apply-selected" :disabled="blocked || !selected.length" @click="prepare()">应用所选状态</button>
        </div>
      </details>
      <div class="risk-options">
        <label><input v-model="highRisk" type="checkbox" :disabled="blocked" />高危操作（需 OTP）</label>
        <div v-if="highRisk"><input v-model="otp" aria-label="OTP 验证码" placeholder="6位验证码" maxlength="6" /><button type="button" @click="requestOtp">发送验证码</button></div>
      </div>
      <p v-if="!info.enabled" class="hint">设备采集已停用，请先启用设备。</p>
    </template>
    <p v-if="status" class="execution-status" role="status">{{ status }}</p>
    <ul v-if="result?.execution?.channels?.length" class="execution-details">
      <li v-for="channel in result.execution.channels" :key="channel.channel">开关{{ channel.channel }}：{{ channel.high ? '闭合' : '断开' }} — {{ channel.phase === 'verified' ? '回读已确认' : '状态未确认' }}</li>
    </ul>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="refreshError" role="alert">{{ refreshError }}</p>
    <ConfirmDialog v-model="showConfirm" title="确认开关控制" :message="`${devNumber}：${confirmation}。确认后下发指令，其他通道保持不变。`" confirm-text="确认下发" :type-to-confirm="confirmed?.highRisk ? devNumber : undefined" danger @confirm="submit" />
  </section>
</template>

<style scoped>
.do-panel { display: flex; flex-direction: column; gap: 12px; margin-bottom: 20px; color: var(--color-text, #1f2937); }
.do-panel p { margin: 0; font-size: 14px; line-height: 1.5; }
.panel-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 20px; }
h3, h4 { margin: 0; }
h3 { font-size: 18px; }
h4 { font-size: 16px; }
.panel-heading p, .hint, small, .channel-heading span { color: var(--color-text-secondary, #64748b); }
.digital-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.digital-grid.with-inputs { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.digital-channel { padding: 16px; border: 1px solid #dce3eb; border-radius: 8px; background: #fff; min-width: 0; }
.channel-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 8px; margin-bottom: 12px; }
.channel-heading span, small { font-size: 12px; }
.channel-state { display: flex; align-items: center; gap: 7px; font-weight: 600; min-height: 30px; }
.channel-state b { margin-left: auto; font-size: 24px; font-variant-numeric: tabular-nums; }
.state-dot { width: 9px; height: 9px; flex-shrink: 0; border-radius: 50%; background: #64748b; }
[data-high='true'] .state-dot { background: #19703b; }
[data-high='true'] .channel-state { color: #166534; }
[data-stale='true'] { background: #f8fafc; }
[data-stale='true'] .channel-state { color: #64748b; }
.channel-actions { display: flex; gap: 8px; margin-top: 14px; }
.channel-actions button { flex: 1; }
button, select { min-height: 44px; border: 1px solid #bac5d2; border-radius: 5px; padding: 8px 12px; background: #fff; color: inherit; font-size: 14px; cursor: pointer; }
button.close-output { background: var(--color-primary, #1677ff); border-color: var(--color-primary, #1677ff); color: #fff; }
button:hover:enabled { filter: brightness(.95); }
button:focus-visible, select:focus-visible, summary:focus-visible { outline: 3px solid #2563eb; outline-offset: 3px; }
button:disabled, select:disabled { opacity: .5; cursor: not-allowed; }
.multi-channel { border-top: 1px solid #e5e7eb; padding-top: 10px; }
summary { cursor: pointer; font-size: 14px; padding: 6px 0; }
.multi-options, .risk-options { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 16px; }
.multi-options { margin-top: 10px; }
.multi-options label { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.risk-options { font-size: 13px; }
.risk-options input:not([type='checkbox']) { padding: 8px; min-height: 44px; }
.execution-status { padding: 10px 12px; border-left: 3px solid var(--color-primary, #1677ff); background: #f0f6ff; }
.execution-details { margin: 0; padding-left: 20px; font-size: 14px; }
[role='alert'] { color: #b42318; }
@media (max-width: 1000px) { .digital-grid.with-inputs { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 480px) { .digital-grid, .digital-grid.with-inputs { grid-template-columns: minmax(0, 1fr); } }
</style>
