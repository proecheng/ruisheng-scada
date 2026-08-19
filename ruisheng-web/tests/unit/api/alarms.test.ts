import { beforeEach, describe, expect, it } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import {
  createAlarmConfig,
  createAlarmSubscription,
  deleteAlarmSubscription,
  listAlarmSubscriptions,
  listDeliveryAudit,
  updateAlarmConfig,
} from '@/api/alarms'
import { apiClient } from '@/api/client'

describe('alarms api', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = new MockAdapter(apiClient)
  })

  it('posts backend point id when creating an alarm config', async () => {
    mock.onPost('/devices/D1/alarms/configs').reply((config) => {
      expect(JSON.parse(String(config.data))).toMatchObject({
        point_id: 7,
        alarm_name: 'temperature high',
        alarm_type: '>',
        limit_value: 80,
      })
      expect(JSON.parse(String(config.data))).not.toHaveProperty('phone_alarm')
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 9,
            point_id: 7,
            alarm_name: 'temperature high',
            alarm_type: '>',
            limit_value: 80,
            phone_alarm: 1,
          },
        },
      ]
    })

    await createAlarmConfig('D1', {
      point_id: 7,
      alarm_name: 'temperature high',
      alarm_type: '>',
      limit: 80,
      severity: 'warning',
      channels: ['wechat'],
    })
  })

  it('keeps relation point mapping when updating an interlock config', async () => {
    mock.onPut('/devices/D1/alarms/configs/9').reply((config) => {
      expect(JSON.parse(String(config.data))).toMatchObject({
        alarm_type: 'LX',
        relation_point_id: 8,
        relation_reg_bit: 2,
        relation_alarm_type: '=',
        relation_limit_value: 1,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 9,
            point_id: 7,
            alarm_name: 'interlock',
            alarm_type: 'LX',
            limit_value: 1,
            relation_point_id: 8,
            relation_reg_bit: 2,
            relation_alarm_type: '=',
            relation_limit_value: 1,
            phone_alarm: 0,
          },
        },
      ]
    })

    await updateAlarmConfig('D1', 9, {
      alarm_type: 'LX',
      relation_point_id: 8,
      relation_reg_bit: 2,
      relation_alarm_type: '=',
      relation_limit_value: 1,
    })
  })

  it('sends explicit nulls when clearing an interlock config', async () => {
    mock.onPut('/devices/D1/alarms/configs/9').reply((config) => {
      expect(JSON.parse(String(config.data))).toMatchObject({
        relation_point_id: null,
        relation_reg_bit: null,
        relation_alarm_type: null,
        relation_limit_value: null,
      })
      return [
        200,
        {
          code: 0,
          data: {
            id: 9,
            point_id: 7,
            alarm_name: 'plain',
            alarm_type: '>',
            limit_value: 80,
            relation_point_id: null,
            relation_reg_bit: null,
            relation_alarm_type: null,
            relation_limit_value: null,
            phone_alarm: 0,
          },
        },
      ]
    })

    const updated = await updateAlarmConfig('D1', 9, {
      relation_point_id: null,
      relation_reg_bit: null,
      relation_alarm_type: null,
      relation_limit_value: null,
    })
    expect(updated.relation_point_id).toBeNull()
    expect(updated.relation_alarm_type).toBeNull()
  })

  it('manages explicit subscriptions and returns sanitized audit rows', async () => {
    const base = '/devices/D1/alarms/configs/9'
    const subscription = {
      id: 5,
      alarm_cfg_id: 9,
      user_name: 'alice',
      channel: 'email',
      created_at: '2026-08-18T00:00:00Z',
    }
    mock.onGet(`${base}/subscriptions`).reply(200, {
      code: 0,
      data: { items: [subscription] },
    })
    mock.onPost(`${base}/subscriptions`).reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({ user_name: 'alice', channel: 'email' })
      return [200, { code: 0, data: subscription }]
    })
    mock.onDelete(`${base}/subscriptions/5`).reply(200, { code: 0, data: { deleted: 5 } })
    mock.onGet(`${base}/delivery-audit`).reply(200, {
      code: 0,
      data: {
        items: [{ id: 8, channel: 'email', status: 'failed', attempt_count: 1 }],
      },
    })

    expect(await listAlarmSubscriptions('D1', 9)).toEqual([subscription])
    expect(await createAlarmSubscription('D1', 9, 'alice', 'email')).toEqual(subscription)
    await expect(deleteAlarmSubscription('D1', 9, 5)).resolves.toBeUndefined()
    expect(await listDeliveryAudit('D1', 9)).toEqual([
      { id: 8, channel: 'email', status: 'failed', attempt_count: 1 },
    ])
  })
})
