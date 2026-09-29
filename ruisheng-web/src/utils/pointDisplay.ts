export interface DigitalChannel {
  number: number
  high: boolean
}

// JSON/JavaScript numbers can expose their binary floating-point tail when
// converted with String(), even though the measured value is 57.91. Keep the
// stored value unchanged and canonicalize only the user-facing text.
const numberFormatter = new Intl.NumberFormat('en-US', {
  useGrouping: false,
  maximumFractionDigits: 12,
})

function formatNumericValue(value: number): string {
  return numberFormatter.format(value)
}

/** Width is a range check, not a mask: never silently discard unexpected high bits. */
export function digitalChannels(value: number | null, width?: number | null): DigitalChannel[] {
  if (!Number.isInteger(width) || !width || width < 1 || width > 16 ||
      value === null || !Number.isInteger(value) || value < 0 || value >= 2 ** width) return []
  return Array.from({ length: width }, (_, index) => ({
    number: index + 1,
    high: (value & (1 << index)) !== 0,
  }))
}

export function formatPointValue(value: number | null, width?: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  if (width === null || width === undefined) return formatNumericValue(value)
  if (digitalChannels(value, width).length === 0) return `状态超出范围（原值 ${formatNumericValue(value)}）`
  return value.toString(2).padStart(width, '0')
}

export function digitalLevelSummary(value: number | null, width?: number | null): string {
  return digitalChannels(value, width)
    .map(channel => `第${channel.number}路：${channel.high ? '高电平' : '低电平'}`)
    .join('；')
}
