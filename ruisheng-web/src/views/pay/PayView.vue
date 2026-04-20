<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { createOrder, listOrders, getOrder, type PayOrder } from '@/api/pay'
import { useAsync } from '@/composables/useAsync'
import { useToast } from '@/composables/useToast'
import LoadingSkeleton from '@/components/LoadingSkeleton.vue'
import EmptyState from '@/components/EmptyState.vue'

const toast = useToast()

const devNumber = ref('')
const amountYuan = ref<number>(10)
const body = ref('设备充值')
const tradeType = ref<'NATIVE' | 'JSAPI'>('NATIVE')

const orders = ref<PayOrder[]>([])
const activeOrder = ref<PayOrder | null>(null)
const loader = useAsync(() => listOrders())

let pollTimer: ReturnType<typeof setInterval> | null = null

async function reload(): Promise<void> {
  orders.value = await loader.run()
}

onMounted(() => reload())
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

async function submit(): Promise<void> {
  if (!devNumber.value || amountYuan.value <= 0) {
    toast.error('请输入设备号和金额（> 0）')
    return
  }
  try {
    const order = await createOrder({
      dev_number: devNumber.value,
      body: body.value,
      total_fee: Math.round(amountYuan.value * 100),
      trade_type: tradeType.value,
    })
    activeOrder.value = order
    toast.info('订单已创建，请完成支付')
    startPolling(order.out_trade_no)
    await reload()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '下单失败')
  }
}

function startPolling(out_trade_no: string): void {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const o = await getOrder(out_trade_no)
      activeOrder.value = o
      if (o.status === 'paid') {
        toast.success('支付成功！')
        clearInterval(pollTimer!)
        pollTimer = null
        await reload()
      } else if (o.status === 'expired' || o.status === 'cancelled') {
        toast.warning(`订单已 ${o.status}`)
        clearInterval(pollTimer!)
        pollTimer = null
        await reload()
      }
    } catch {
      /* keep polling */
    }
  }, 3000)
}

const fmtYuan = (fen: number) => (fen / 100).toFixed(2)
</script>

<template>
  <section class="pay">
    <header>
      <h2>设备充值</h2>
    </header>

    <div class="grid">
      <section class="panel">
        <h3>创建订单</h3>
        <form class="form" @submit.prevent="submit">
          <label>设备号 <input v-model="devNumber" type="text" required /></label>
          <label>金额（元） <input v-model.number="amountYuan" type="number" min="1" step="1" /></label>
          <label>
            支付方式
            <select v-model="tradeType">
              <option value="NATIVE">扫码支付（PC）</option>
              <option value="JSAPI">公众号 JSAPI</option>
            </select>
          </label>
          <label>描述 <input v-model="body" type="text" /></label>
          <button type="submit">下单</button>
        </form>

        <div v-if="activeOrder" class="active">
          <h4>订单 {{ activeOrder.out_trade_no }}</h4>
          <p>状态：<strong>{{ activeOrder.status }}</strong></p>
          <p>金额：¥{{ fmtYuan(activeOrder.total_fee) }}</p>
          <div v-if="activeOrder.code_url && activeOrder.status === 'pending'" class="qr-wrap">
            <p>请用微信扫描二维码完成支付：</p>
            <img
              :src="`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(activeOrder.code_url)}`"
              alt="微信支付二维码"
            />
          </div>
        </div>
      </section>

      <section class="panel">
        <h3>订单记录</h3>
        <LoadingSkeleton v-if="loader.isPending.value" :lines="3" />
        <EmptyState v-else-if="orders.length === 0" title="暂无订单" />
        <ul v-else class="order-list">
          <li v-for="o in orders" :key="o.out_trade_no" :class="o.status">
            <div class="o-head">
              <code>{{ o.out_trade_no.slice(0, 16) }}…</code>
              <span class="status">{{ o.status }}</span>
            </div>
            <div class="o-body">
              <span>{{ o.dev_number }}</span>
              <span>¥{{ fmtYuan(o.total_fee) }}</span>
              <span>{{ new Date(o.created_at).toLocaleString() }}</span>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.pay { display: flex; flex-direction: column; gap: 12px; }
header { background: #fff; padding: 12px 16px; border-radius: 6px; }
h2 { font-size: 18px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.panel { background: #fff; padding: 16px; border-radius: 6px; }
.panel h3 { font-size: 15px; margin-bottom: 10px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.form input, .form select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; }
.form button { background: var(--color-primary); color: white; border: none; padding: 8px; border-radius: 4px; cursor: pointer; }
.active { margin-top: 16px; padding: 12px; background: #f7f7f7; border-radius: 6px; }
.qr-wrap { margin-top: 10px; text-align: center; }
.order-list { list-style: none; display: flex; flex-direction: column; gap: 8px; }
.order-list li { border: 1px solid #eee; border-radius: 4px; padding: 8px; font-size: 12px; }
.order-list li.paid { border-left: 3px solid var(--color-success); }
.order-list li.pending { border-left: 3px solid var(--color-warning); }
.order-list li.cancelled, .order-list li.expired { opacity: 0.6; }
.o-head { display: flex; justify-content: space-between; margin-bottom: 4px; }
.status { color: var(--color-primary); }
.o-body { display: flex; justify-content: space-between; color: var(--color-text-secondary); }
@media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
</style>
