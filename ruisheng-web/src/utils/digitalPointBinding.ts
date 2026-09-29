import type { Device } from '@/api/devices'
import type { PointConfig } from '@/api/points'

/** Bind the confirmed two-channel board by its point contract, never by label or DB id. */
export function twoChannelPoint(points: PointConfig[], device: Device | null, address: 0 | 1): PointConfig | null {
  if (device?.transport_type !== 'serial' || device.read_profile !== 'zero_origin_38') return null
  const matches = points.filter(p => p.register_address === address && p.fun_code === 3 &&
    p.dev_addr === device.modbus_addr && p.data_type === '字' && p.r_bit == null &&
    p.display_bits === 2 && p.raw_ratio === 1 && p.raw_offset === 0 && p.ratio === 1 && p.offset === 0)
  return matches.length === 1 ? matches[0]! : null
}
