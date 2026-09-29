import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DeviceDetailView from '@/views/devices/DeviceDetailView.vue'
import { getDevice, getRealtime } from '@/api/devices'
import { useWsStore } from '@/stores/ws'
import { useAuthStore } from '@/stores/auth'
import { listPoints } from '@/api/points'
import { getDoInfo } from '@/api/doControl'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn(), back: vi.fn() }) }))
vi.mock('@/api/devices', () => ({ getDevice: vi.fn(), getRealtime: vi.fn(), setDeviceEnabled: vi.fn() }))
vi.mock('@/api/points', () => ({ listPoints: vi.fn() }))
vi.mock('@/api/doControl', () => ({ getDoInfo: vi.fn(), getDoResult: vi.fn(), setDoLevels: vi.fn() }))

const ts = '2026-09-15T03:20:00Z'
function snapshot(dev = 'DEV001', value: number | null = 56000, time = ts) {
  return { dev_number: dev, points: [
    { point_id: 21, point_name: '寄存器20', value, ts: time },
    { point_id: 32, point_name: '寄存器31', value: 0, ts: time },
  ] }
}
const makeView = () => mount(DeviceDetailView, { props: { devNumber: 'DEV001' }, global: { directives: { permission: {} } } })

describe('device realtime lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(ts))
    setActivePinia(createPinia())
    vi.mocked(getDevice).mockReset().mockImplementation(async (dev) => ({ dev_number: dev, dev_name: dev, state: 'online', update_interval_decisec: 50 }))
    vi.mocked(getRealtime).mockReset().mockImplementation(async (dev) => snapshot(dev))
    vi.mocked(listPoints).mockReset().mockResolvedValue([])
  })
  afterEach(() => vi.useRealTimers())

  it('keeps values on malformed pushes and processes every point in a burst', async () => {
    const wrapper = makeView(); await flushPromises()
    const ws = useWsStore()
    ws.pushMessage({ type: 'realtime', dev_number: 'DEV001', point_id: 21, value: 0, ts: '' })
    await flushPromises()
    expect(wrapper.find('.p-value').text()).toBe('56000')
    expect(wrapper.text()).not.toContain('NaN')
    for (const point_id of [21, 32]) ws.pushMessage({ type: 'realtime', dev_number: 'DEV001', point_id, value: point_id, ts: '2026-09-15T03:20:05Z' })
    await flushPromises()
    expect(wrapper.findAll('.p-value').map(p => p.text())).toEqual(['21', '32'])
    wrapper.unmount()
  })

  it('reconciles each five seconds even with an open socket and keeps cards while pending or failed', async () => {
    useWsStore().setState('open')
    const wrapper = makeView(); await flushPromises()
    let finish!: (s: ReturnType<typeof snapshot>) => void
    vi.mocked(getRealtime).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(getRealtime).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.point-card')).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(10000)
    expect(getRealtime).toHaveBeenCalledTimes(2)
    finish(snapshot('DEV001', 42, '2026-09-15T03:20:15Z')); await flushPromises()
    vi.mocked(getRealtime).mockRejectedValueOnce(new Error('network'))
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.find('.p-value').text()).toBe('42')
    expect(wrapper.find('[role="status"]').text()).toContain('保留上次数据')
    wrapper.unmount(); expect(vi.getTimerCount()).toBe(0)
  })

  it('does not let an older snapshot overwrite a fresh push and updates sample age every second', async () => {
    const wrapper = makeView(); await flushPromises()
    const ws = useWsStore()
    ws.pushMessage({ type: 'realtime', dev_number: 'DEV001', point_id: 21, value: 99, ts: '2026-09-15T03:20:02Z' })
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.find('.p-value').text()).toBe('99')
    expect(wrapper.find('.p-ts').text()).toBe('3 秒前')
    wrapper.unmount()
  })

  it('retains real zero but displays unknown values and times explicitly', async () => {
    vi.mocked(getRealtime).mockResolvedValueOnce(snapshot('DEV001', null, ''))
    const wrapper = makeView(); await flushPromises()
    expect(wrapper.findAll('.p-value').map(p => p.text())).toEqual(['—', '0'])
    expect(wrapper.find('.p-ts').text()).toBe('暂无采集时间')
    wrapper.unmount()
  })

  it('ignores completion after navigation and never creates an orphan refresh timer', async () => {
    let finish!: (s: ReturnType<typeof snapshot>) => void
    vi.mocked(getRealtime).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const wrapper = makeView(); await wrapper.setProps({ devNumber: 'DEV002' }); await flushPromises()
    finish(snapshot('DEV001', 999)); await flushPromises()
    expect(wrapper.find('.p-value').text()).toBe('56000')
    expect(wrapper.text()).toContain('DEV002')
    wrapper.unmount()
    const calls = vi.mocked(getRealtime).mock.calls.length
    await vi.advanceTimersByTimeAsync(20000)
    expect(getRealtime).toHaveBeenCalledTimes(calls)
  })

  it('marks unchanged old digital samples as stale and restores current levels on new samples', async () => {
    vi.mocked(getRealtime).mockResolvedValue({ dev_number: 'DEV001', points: [
      { point_id: 1, point_name: 'DI', value: 3, ts, display_bits: 2 },
      { point_id: 2, point_name: 'DO', value: 0, ts, display_bits: 2 },
    ] })
    const wrapper = makeView(); await flushPromises()
    expect(wrapper.find('.point-card').attributes('data-stale')).toBe('false')
    expect(wrapper.findAll('.p-channels [data-high="true"]')).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(15000)
    expect(wrapper.findAll('.point-card[data-stale="true"]')).toHaveLength(2)
    expect(wrapper.findAll('.p-channels [data-high="true"]')).toHaveLength(0)
    expect(wrapper.find('.p-channels').text()).toContain('上次第1路：高电平')
    expect(wrapper.text()).toContain('数据未更新，等待设备新采样')

    const ws = useWsStore()
    for (const [point_id, value] of [[1, 0], [2, 3]] as const) {
      ws.pushMessage({ type: 'realtime', dev_number: 'DEV001', point_id, value, ts: new Date().toISOString() })
    }
    await flushPromises()
    expect(wrapper.findAll('.p-value').map(p => p.text())).toEqual(['00', '11'])
    expect(wrapper.findAll('.point-card[data-stale="true"]')).toHaveLength(0)
    expect(wrapper.findAll('.p-channels [data-high="true"]')).toHaveLength(2)
    wrapper.unmount()
  })

  it('refreshes both digital states from snapshots when the socket misses changes', async () => {
    const digital = (value: number, time: string) => ({ dev_number: 'DEV001', points: [
      { point_id: 1, point_name: 'DI', value, ts: time, display_bits: 2 },
      { point_id: 2, point_name: 'DO', value, ts: time, display_bits: 2 },
    ] })
    vi.mocked(getRealtime).mockResolvedValueOnce(digital(3, ts))
    const wrapper = makeView(); await flushPromises()
    for (const value of [0, 1, 2, 3]) {
      vi.mocked(getRealtime).mockResolvedValueOnce(digital(value, new Date(Date.now() + 5000).toISOString()))
      await vi.advanceTimersByTimeAsync(5000)
      expect(wrapper.findAll('.p-value').map(p => p.text())).toEqual(Array(2).fill(value.toString(2).padStart(2, '0')))
    }
    wrapper.unmount()
  })

  it('puts four mapped channels and output buttons on the realtime page and follows polling', async () => {
    useAuthStore().user = { user_name: 'test', authority: 'Administrators', usr_group: 'test', control_authority: 1 }
    vi.mocked(getDevice).mockResolvedValue({ dev_number: 'DEV001', dev_name: 'Pump', state: 'online', transport_type: 'serial', read_profile: 'zero_origin_38', modbus_addr: 1 })
    vi.mocked(listPoints).mockResolvedValue([0, 1].map((register_address, i) => ({ point_id: [71, 92][i]!, point_name: 'custom', register_address, fun_code: 3, dev_addr: 1, data_type: '字', display_bits: 2, raw_ratio: 1, raw_offset: 0, ratio: 1, offset: 0 })))
    const digital = (value: number, time: string) => ({ dev_number: 'DEV001', points: [
      { point_id: 71, point_name: 'custom input', value, ts: time, display_bits: 2 },
      { point_id: 92, point_name: 'custom output', value, ts: time, display_bits: 2 },
      { point_id: 30, point_name: 'temperature', value: 21, ts: time },
    ] })
    vi.mocked(getRealtime).mockResolvedValue(digital(0, ts))
    vi.mocked(getDoInfo).mockResolvedValue({ supported: true, config_version: 27, enabled: true, channels: [1, 2], sample: { org_value: 0, recorded_at: ts } })
    const wrapper = makeView(); await flushPromises()
    expect(wrapper.findAll('.digital-channel')).toHaveLength(4)
    expect(wrapper.findAll('.channel-actions button')).toHaveLength(4)
    expect(wrapper.findAll('.point-card')).toHaveLength(1)
    expect(wrapper.get('.point-card').text()).toContain('temperature')
    for (const value of [1, 2, 3]) {
      vi.mocked(getRealtime).mockResolvedValue(digital(value, new Date(Date.now() + 5000).toISOString()))
      await vi.advanceTimersByTimeAsync(5000)
      expect(wrapper.findAll('.digital-channel .channel-state b').map(e => e.text())).toEqual([String(value & 1), String((value >> 1) & 1), String(value & 1), String((value >> 1) & 1)])
    }
    wrapper.unmount(); expect(vi.getTimerCount()).toBe(0)
  })
})
