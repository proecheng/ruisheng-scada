import type { DeviceReadProfile } from '@/api/devices'
import type { PointConfig } from '@/api/points'

/** Reject template points that the selected read profile cannot poll, before creating the device. */
export function incompatibleTemplatePoint(
  profile: DeviceReadProfile,
  points: PointConfig[],
): string | null {
  if (profile !== 'zero_origin_38') return null
  for (const point of points) {
    const span = point.data_type === '双字' ? 2 : 1
    const last = 38 - span
    if (point.fun_code !== 3 || point.register_address < 0 || point.register_address > last) {
      const name = point.point_name || '未命名'
      return `模板点位“${name}”与自研设备38寄存器方案不兼容：需要 FC3，且寄存器地址在 0～${last}`
    }
  }
  return null
}
