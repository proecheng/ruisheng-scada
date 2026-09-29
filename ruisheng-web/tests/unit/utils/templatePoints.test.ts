import { describe, expect, it } from 'vitest'
import type { PointConfig } from '@/api/points'
import { incompatibleTemplatePoint } from '@/utils/templatePoints'

function point(overrides: Partial<PointConfig> = {}): PointConfig {
  return {
    point_id: 1,
    point_name: '电压',
    register_address: 0,
    fun_code: 3,
    dev_addr: 1,
    data_type: '字',
    raw_ratio: 1,
    raw_offset: 0,
    ratio: 1,
    offset: 0,
    ...overrides,
  }
}

describe('incompatibleTemplatePoint', () => {
  it('accepts a zero-origin FC3 word inside 0..37', () => {
    expect(incompatibleTemplatePoint('zero_origin_38', [point({ register_address: 37 })])).toBeNull()
  })

  it('rejects FC4 and an address outside the fixed block before the device is created', () => {
    expect(incompatibleTemplatePoint('zero_origin_38', [point({ fun_code: 4 })])).toContain('FC3')
    expect(incompatibleTemplatePoint('zero_origin_38', [point({ register_address: 38 })])).toContain('0～37')
    expect(
      incompatibleTemplatePoint('zero_origin_38', [point({ data_type: '双字', register_address: 37 })]),
    ).toContain('0～36')
  })

  it('does not restrict grouped profiles', () => {
    expect(incompatibleTemplatePoint('point_groups', [point({ fun_code: 4, register_address: 80 })])).toBeNull()
  })
})
