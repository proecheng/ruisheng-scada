import { describe, it, expect, beforeEach } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { apiClient } from '@/api/client'
import { createPoint, listPoints, updatePoint } from '@/api/points'

describe('points api', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = new MockAdapter(apiClient)
  })

  it('maps backend Modbus fields for the UI', async () => {
    mock.onGet('/devices/D1/points').reply(200, {
      code: 0,
      message: 'ok',
      data: {
        items: [
          {
            id: 7,
            point_name: 'pressure',
            user_point_name: '压力',
            point_number: 12,
            fun_code: 4,
            dev_addr: 1,
            r_bit: null,
            value_type: '双字',
            point_unit: 'MPa',
            point_ratio: 0.1,
            point_offset: 1,
            user_ratio: 2,
            user_point_offset: 3,
            min_value: 0,
            max_value: 100,
            show: 1,
          },
        ],
      },
    })

    const points = await listPoints('D1')
    expect(points[0]).toMatchObject({
      point_id: 7,
      point_name: '压力',
      register_address: 12,
      fun_code: 4,
      data_type: '双字',
      raw_ratio: 0.1,
      raw_offset: 1,
      ratio: 2,
      offset: 3,
    })
  })

  it('posts explicit register contract when creating a point', async () => {
    mock.onPost('/devices/D1/points').reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({
        point_name: 'pump_running',
        user_point_name: 'pump_running',
        point_number: 20,
        fun_code: 3,
        dev_addr: 1,
        r_bit: 2,
        value_type: 'bit',
        point_ratio: 1,
        point_offset: 0,
        user_ratio: 1,
        user_point_offset: 0,
        show: 1,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 8,
            point_name: 'pump_running',
            user_point_name: 'pump_running',
            point_number: 20,
            fun_code: 3,
            dev_addr: 1,
            r_bit: 2,
            value_type: 'bit',
            point_ratio: 1,
            point_offset: 0,
            user_ratio: 1,
            user_point_offset: 0,
          },
        },
      ]
    })

    await createPoint('D1', {
      point_id: 8,
      point_name: 'pump_running',
      register_address: 20,
      fun_code: 3,
      dev_addr: 1,
      r_bit: 2,
      data_type: 'bit',
      raw_ratio: 1,
      raw_offset: 0,
      ratio: 1,
      offset: 0,
      show: true,
    })
  })

  it('clears r_bit when updating a point back to word type', async () => {
    mock.onPut('/devices/D1/points/8').reply((config) => {
      expect(JSON.parse(String(config.data))).toMatchObject({
        point_name: 'temperature',
        point_number: 30,
        fun_code: 3,
        r_bit: null,
        value_type: '字',
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 8,
            point_name: 'temperature',
            point_number: 30,
            fun_code: 3,
            dev_addr: 1,
            r_bit: null,
            value_type: '字',
            point_ratio: 1,
            point_offset: 0,
            user_ratio: 1,
            user_point_offset: 0,
          },
        },
      ]
    })

    await updatePoint('D1', 8, {
      point_name: 'temperature',
      register_address: 30,
      fun_code: 3,
      dev_addr: 1,
      data_type: '字',
      raw_ratio: 1,
      raw_offset: 0,
      ratio: 1,
      offset: 0,
    })
  })
})
