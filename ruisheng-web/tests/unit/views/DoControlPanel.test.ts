import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DoControlPanel from '@/components/DoControlPanel.vue'
import { getDoInfo, getDoResult, setDoLevels } from '@/api/doControl'

vi.mock('@/api/doControl', () => ({ getDoInfo: vi.fn(), getDoResult: vi.fn(), setDoLevels: vi.fn() }))
vi.mock('@/api/auth', () => ({ otpSend: vi.fn() }))
const ts = '2026-09-21T08:00:00Z'
const makeInfo = (version = 26) => ({ supported: true, config_version: version, channels: [1, 2], enabled: true, sample: { org_value: 2, recorded_at: ts } })
const makeView = () => mount(DoControlPanel, { props: { devNumber: 'DEV001' } })

describe('DO confirmed per-channel control', () => {
  beforeEach(() => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(ts)); vi.resetAllMocks()
    vi.mocked(getDoInfo).mockResolvedValue(makeInfo())
    vi.mocked(setDoLevels).mockResolvedValue({ cmd_id: 'cmd1' })
    vi.mocked(getDoResult).mockResolvedValue({ cmd_id: 'cmd1', result: 'pending' })
  })
  afterEach(() => vi.useRealTimers())

  it('defaults to keep, confirms only selected channel, and waits for actual result', async () => {
    const w = makeView(); await flushPromises()
    expect(w.findAll('select').map(e => (e.element as HTMLSelectElement).value)).toEqual(['keep', 'keep'])
    expect(w.findAll('.do-channel').map(e => e.text())).toEqual([expect.stringContaining('已断开'), expect.stringContaining('已闭合')])
    await w.findAll('select')[0]!.setValue('high')
    await w.find('.apply-selected').trigger('click')
    expect(w.find('[role="dialog"]').text()).toContain('开关1（DO1）→ 闭合（1）')
    expect(w.find('[role="dialog"]').text()).not.toContain('开关2（DO2）→')
    expect(w.find('.type-to-confirm input').exists()).toBe(false)
    await w.find('button.confirm').trigger('click'); await flushPromises()
    expect(setDoLevels).toHaveBeenCalledWith('DEV001', [{ number: 1, high: true }], 26, false, '')
    expect(w.find('[role="status"]').text()).toContain('等待逐路回读')
    expect(w.text()).not.toContain('执行成功，设备回读已确认')
    vi.mocked(getDoResult).mockResolvedValue({ cmd_id: 'cmd1', result: 'timeout', execution: { channels: [{ channel: 1, high: true, phase: 'sending' }] } })
    await vi.advanceTimersByTimeAsync(1500)
    expect(w.text()).toContain('未自动重发')
    expect(setDoLevels).toHaveBeenCalledTimes(1)
    w.unmount(); expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects configuration change while confirmation is open', async () => {
    const w = makeView(); await flushPromises()
    await w.findAll('select')[1]!.setValue('low')
    await w.find('.apply-selected').trigger('click')
    vi.mocked(getDoInfo).mockResolvedValue(makeInfo(27))
    await vi.advanceTimersByTimeAsync(1500)
    expect(w.find('.type-to-confirm input').exists()).toBe(false)
    await w.find('button.confirm').trigger('click'); await flushPromises()
    expect(setDoLevels).not.toHaveBeenCalled()
    expect(w.text()).toContain('配置已改变')
    w.unmount()
  })

  it('ages readback even when network requests fail and stops after navigation', async () => {
    const w = makeView(); await flushPromises()
    vi.mocked(getDoInfo).mockRejectedValue(new Error('network'))
    await vi.advanceTimersByTimeAsync(16000)
    expect(w.findAll('.do-channel').every(e => e.text().includes('回读未更新'))).toBe(true)
    w.unmount(); expect(vi.getTimerCount()).toBe(0)
  })

  it('single-channel buttons ignore another channel draft and never change readback optimistically', async () => {
    const w = makeView(); await flushPromises()
    await w.findAll('select')[1]!.setValue('low')
    await w.get('[aria-label="闭合开关1"]').trigger('click')
    expect(w.get('[role="dialog"]').text()).toContain('开关1（DO1）→ 闭合（1）')
    expect(w.get('[role="dialog"]').text()).not.toContain('开关2（DO2）→')
    expect(setDoLevels).not.toHaveBeenCalled()
    expect(w.find('.type-to-confirm input').exists()).toBe(false)
    await w.get('button.confirm').trigger('click'); await flushPromises()
    expect(setDoLevels).toHaveBeenCalledWith('DEV001', [{ number: 1, high: true }], 26, false, '')
    expect(w.get('[aria-label="开关1输出控制"] .channel-state').text()).toContain('已断开')
    expect(w.get('[aria-label="闭合开关2"]').attributes('disabled')).toBeDefined()
    vi.mocked(getDoInfo).mockResolvedValue({ ...makeInfo(), sample: { org_value: 3, recorded_at: ts } })
    vi.mocked(getDoResult).mockResolvedValue({ cmd_id: 'cmd1', result: 'success', execution: { readback: 3, channels: [{ channel: 1, high: true, phase: 'verified' }] } })
    await vi.advanceTimersByTimeAsync(1500)
    expect(w.get('[aria-label="开关1输出控制"] .channel-state').text()).toContain('已闭合')
    expect(w.get('[role="status"]').text()).toContain('执行成功，设备回读已确认')
    expect(setDoLevels).toHaveBeenCalledTimes(1)
    w.unmount()
  })

  it('shows all four live bits to read-only users without calling control endpoints', async () => {
    const w = mount(DoControlPanel, { props: { devNumber: 'DEV001', canControl: false, showInputs: true,
      inputSample: { point_id: 71, value: 2, ts }, outputSample: { point_id: 92, value: 1, ts } } })
    await flushPromises()
    expect(w.findAll('.input-channel .channel-state').map(e => e.text())).toEqual(['低电平 0', '高电平 1'])
    expect(w.findAll('.do-channel .channel-state').map(e => e.text())).toEqual(['已闭合 1', '已断开 0'])
    expect(w.findAll('.channel-actions button').every(e => e.attributes('disabled') !== undefined)).toBe(true)
    expect(getDoInfo).not.toHaveBeenCalled()
    await w.get('[aria-label="闭合开关2"]').trigger('click')
    expect(setDoLevels).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(15000)
    expect(w.findAll('.digital-channel[data-stale="true"]')).toHaveLength(4)
    w.unmount(); expect(vi.getTimerCount()).toBe(0)
  })

  it('still requires OTP and typed device confirmation for high-risk commands', async () => {
    const w = makeView(); await flushPromises()
    await w.get('input[type="checkbox"]').setValue(true)
    await w.get('[aria-label="闭合开关1"]').trigger('click')
    expect(w.find('[role="dialog"]').exists()).toBe(false)
    expect(w.text()).toContain('高危操作需要 OTP')
    await w.get('[aria-label="OTP 验证码"]').setValue('123456')
    await w.get('[aria-label="闭合开关1"]').trigger('click')
    expect(w.get('button.confirm').attributes('disabled')).toBeDefined()
    await w.get('.type-to-confirm input').setValue('DEV001')
    await w.get('button.confirm').trigger('click'); await flushPromises()
    expect(setDoLevels).toHaveBeenCalledWith('DEV001', [{ number: 1, high: true }], 26, true, '123456')
    w.unmount()
  })

  it.each([
    [[], '操作前状态读取超时，本次未下发控制指令'],
    [[{ channel: 1, high: true, phase: 'sending' }], '写指令应答超时'],
    [[{ channel: 1, high: true, phase: 'acknowledged' }], '状态回读超时'],
  ])('distinguishes timeout stage without changing output state', async (channels, message) => {
    const w = makeView(); await flushPromises()
    await w.get('[aria-label="闭合开关1"]').trigger('click')
    await w.get('button.confirm').trigger('click'); await flushPromises()
    vi.mocked(getDoResult).mockResolvedValue({ cmd_id: 'cmd1', result: 'timeout', execution: {
      reason: 'response_timeout_output_may_have_changed', channels,
    } })
    await vi.advanceTimersByTimeAsync(1500)
    expect(w.get('[role="status"]').text()).toContain(message)
    expect(w.get('[aria-label="开关1输出控制"] .channel-state').text()).toContain('已断开')
    expect(setDoLevels).toHaveBeenCalledTimes(1)
    w.unmount()
  })
})
