import { beforeEach, describe, expect, it } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { createAlarmConfig, updateAlarmConfig } from '@/api/alarms'
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
        phone_alarm: 1,
      })
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
            phone_alarm: 0,
          },
        },
      ]
    })

    await updateAlarmConfig('D1', 9, {
      alarm_type: 'LX',
      relation_point_id: 8,
    })
  })
})
