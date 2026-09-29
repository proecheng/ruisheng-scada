import { describe, expect, it } from 'vitest'
import { twoChannelPoint } from '@/utils/digitalPointBinding'
import type { Device } from '@/api/devices'
import type { PointConfig } from '@/api/points'

const device: Device = { dev_number: 'D', dev_name: 'D', state: 'online', transport_type: 'serial', read_profile: 'zero_origin_38', modbus_addr: 1 }
const point: PointConfig = { point_id: 92, point_name: 'arbitrary label', register_address: 1, fun_code: 3, dev_addr: 1, data_type: '字', r_bit: null, display_bits: 2, raw_ratio: 1, raw_offset: 0, ratio: 1, offset: 0 }
describe('two-channel board binding', () => {
  it('uses the address contract with arbitrary names and database identifiers', () => {
    expect(twoChannelPoint([{ ...point, register_address: 0, point_id: 71 }, point], device, 1)?.point_id).toBe(92)
    expect(twoChannelPoint([point], device, 0)).toBeNull()
  })
  it('refuses unknown boards and ambiguous or scaled values', () => {
    expect(twoChannelPoint([point], { ...device, read_profile: 'point_groups' }, 1)).toBeNull()
    expect(twoChannelPoint([point, { ...point, point_id: 93 }], device, 1)).toBeNull()
    for (const change of [{ ratio: 2 }, { raw_offset: 1 }, { r_bit: 0 }, { dev_addr: 2 }, { display_bits: 8 }]) {
      expect(twoChannelPoint([{ ...point, ...change }], device, 1)).toBeNull()
    }
  })
})
