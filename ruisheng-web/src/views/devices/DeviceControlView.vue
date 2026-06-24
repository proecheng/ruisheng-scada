<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { sendControl, cancelCommand } from '@/api/control'
import { otpSend } from '@/api/auth'
import { useToast } from '@/composables/useToast'
import { useWsStore } from '@/stores/ws'
import { useAuthStore } from '@/stores/auth'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const props = defineProps<{ devNumber: string }>()
const router = useRouter()
const toast = useToast()
const wsStore = useWsStore()
const auth = useAuthStore()

const action = ref('start')
const isHighRisk = ref(false)
const showConfirm = ref(false)
const otp = ref('')
const pendingCmdId = ref<string | null>(null)

async function requestOtp(): Promise<void> {
  if (!auth.user) return
  try {
    await otpSend({ channel: 'sms', action: 'control' })
    toast.info('验证码已发送，5 分钟内有效')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'OTP 发送失败')
  }
}

async function onSubmit(): Promise<void> {
  if (isHighRisk.value && !otp.value) {
    toast.error('高危操作需要 OTP 验证码')
    return
  }
  try {
    const ack = await sendControl(props.devNumber, {
      action: action.value,
      high_risk: isHighRisk.value,
      otp: isHighRisk.value ? otp.value : undefined,
    })
    pendingCmdId.value = ack.cmd_id
    toast.info(`已下发命令 ${ack.cmd_id.slice(0, 8)}…，等待设备回复`, { timeoutMs: 4000 })
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '控制失败')
  }
}

async function onCancel(): Promise<void> {
  if (!pendingCmdId.value) return
  try {
    await cancelCommand(pendingCmdId.value)
    toast.success('已取消')
    pendingCmdId.value = null
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '取消失败')
  }
}

watch(
  () => wsStore.lastMessage,
  (m) => {
    if (!m || m.type !== 'control_result') return
    if (m.cmd_id !== pendingCmdId.value) return
    if (m.status === 'success') toast.success('命令执行成功')
    else if (m.status === 'failed') toast.error(`失败：${m.reason ?? '未知'}`)
    else if (m.status === 'timeout') toast.error('超时未响应')
    else if (m.status === 'cancelled') toast.warning(`已取消${m.reason ? '：' + m.reason : ''}`)
    pendingCmdId.value = null
  },
)
</script>

<template>
  <section class="device-control">
    <header>
      <button class="back" @click="router.back()">← 返回</button>
      <h2>{{ devNumber }} — 远程控制</h2>
    </header>

    <form class="form" @submit.prevent="showConfirm = true">
      <label>
        操作
        <select v-model="action">
          <option value="start">启动</option>
          <option value="stop">停止</option>
          <option value="reset">复位</option>
          <option value="set_speed">调速</option>
        </select>
      </label>

      <label class="high-risk">
        <input v-model="isHighRisk" type="checkbox" />
        标记为高危操作（需 OTP 验证码）
      </label>

      <div v-if="isHighRisk" class="otp-box">
        <input v-model="otp" type="text" placeholder="输入 6 位 OTP" maxlength="6" />
        <button type="button" class="otp-btn" @click="requestOtp">发送验证码</button>
      </div>

      <button type="submit" class="submit">下发命令</button>

      <div v-if="pendingCmdId" class="pending">
        <span>等待设备回复 cmd={{ pendingCmdId?.slice(0, 8) }}…</span>
        <button type="button" class="cancel-btn" @click="onCancel">取消命令</button>
      </div>
    </form>

    <ConfirmDialog
      v-model="showConfirm"
      title="确认下发控制命令"
      :message="`即将对设备 ${devNumber} 执行『${action}』。该操作会影响真实设备。`"
      :type-to-confirm="devNumber"
      danger
      @confirm="onSubmit"
    />
  </section>
</template>

<style scoped>
.device-control { background: #fff; padding: 16px; border-radius: 6px; max-width: 560px; }
.back { background: none; border: 1px solid #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; margin-right: 8px; }
header { margin-bottom: 16px; }
h2 { display: inline; font-size: 18px; }
.form { display: flex; flex-direction: column; gap: 16px; }
.form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.form select, .form input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
.high-risk { flex-direction: row !important; align-items: center; gap: 8px; }
.otp-box { display: flex; gap: 8px; }
.otp-box input { flex: 1; }
.otp-btn { padding: 6px 14px; border: 1px solid #ccc; background: #fff; border-radius: 4px; cursor: pointer; }
.submit { background: var(--color-primary); color: white; border: none; padding: 10px; border-radius: 4px; cursor: pointer; font-size: 14px; }
.pending { display: flex; align-items: center; gap: 12px; padding: 10px; background: #fff3e0; border-radius: 4px; font-size: 13px; }
.cancel-btn { background: #fff; border: 1px solid var(--color-error); color: var(--color-error); padding: 4px 10px; border-radius: 3px; cursor: pointer; }
</style>
