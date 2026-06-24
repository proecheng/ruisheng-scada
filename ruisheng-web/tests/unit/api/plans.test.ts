import { beforeEach, describe, expect, it } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import { apiClient } from '@/api/client'
import { listTimingPlans, upsertTimingPlan } from '@/api/plans'

describe('plans api', () => {
  let mock: MockAdapter

  beforeEach(() => {
    mock = new MockAdapter(apiClient)
  })

  it('maps backend timing plan fields directly for UI configuration', async () => {
    mock.onGet('/plans/timing').reply(200, {
      code: 0,
      message: 'ok',
      data: {
        items: [
          {
            id: 61,
            dev_number: 'D1',
            action_at: '2026-05-01T08:00:00Z',
            action: 2,
            repetition: 60,
            enable: true,
            updated_at: '2026-04-30T08:00:00Z',
          },
        ],
      },
    })

    const plans = await listTimingPlans()
    expect(plans[0]).toMatchObject({
      id: 61,
      dev_number: 'D1',
      action: 'stop',
      action_at: '2026-05-01T08:00:00Z',
      repetition: 60,
      enabled: true,
      updated_at: '2026-04-30T08:00:00Z',
    })
  })

  it('posts the backend timing plan contract without unsupported cron or name fields', async () => {
    mock.onPost('/plans/timing').reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({
        dev_number: 'D1',
        action_at: '2026-05-01T08:00:00.000Z',
        action: 1,
        repetition: 0,
        enable: true,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 62,
            dev_number: 'D1',
            action_at: '2026-05-01T08:00:00.000Z',
            action: 1,
            repetition: 0,
            enable: true,
          },
        },
      ]
    })

    await upsertTimingPlan({
      dev_number: 'D1',
      action: 'start',
      action_at: '2026-05-01T08:00:00.000Z',
      repetition: 0,
      enabled: true,
    })
  })

  it('puts repetition and action updates to the backend timing plan contract', async () => {
    mock.onPut('/plans/timing/62').reply((config) => {
      expect(JSON.parse(String(config.data))).toEqual({
        action_at: '2026-05-01T09:00:00.000Z',
        action: 3,
        repetition: 300,
        enable: false,
      })
      return [
        200,
        {
          code: 0,
          message: 'ok',
          data: {
            id: 62,
            dev_number: 'D1',
            action_at: '2026-05-01T09:00:00.000Z',
            action: 3,
            repetition: 300,
            enable: false,
          },
        },
      ]
    })

    await upsertTimingPlan({
      id: 62,
      dev_number: 'D1',
      action: 'reset',
      action_at: '2026-05-01T09:00:00.000Z',
      repetition: 300,
      enabled: false,
    })
  })
})
