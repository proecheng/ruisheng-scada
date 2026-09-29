import { describe, expect, it } from 'vitest'
import { digitalChannels, digitalLevelSummary, formatPointValue } from '@/utils/pointDisplay'

describe('register digital state display', () => {
  it.each([[0, '00'], [1, '01'], [2, '10'], [3, '11']])('shows two channels for %i', (value, expected) => {
    expect(formatPointValue(value as number, 2)).toBe(expected)
  })
  it('keeps the least significant bit as channel one', () => {
    expect(digitalLevelSummary(2, 2)).toBe('第1路：低电平；第2路：高电平')
    expect(digitalChannels(3, 2).every(channel => channel.high)).toBe(true)
  })
  it('supports configured widths through all 16 bits', () => {
    expect(formatPointValue(3, 4)).toBe('0011')
    expect(formatPointValue(0, 8)).toBe('00000000')
    expect(formatPointValue(32768, 16)).toBe('1000000000000000')
    expect(digitalChannels(32768, 16)[15]).toEqual({ number: 16, high: true })
    expect(formatPointValue(65535, 16)).toBe('1111111111111111')
    expect(formatPointValue(1, 1)).toBe('1')
  })
  it.each([-1, 4, 1.5, Infinity, NaN])('never masks or rounds invalid two-channel value %s', value => {
    expect(digitalChannels(value, 2)).toEqual([])
    expect(formatPointValue(value, 2)).not.toMatch(/^[01]{2}$/)
  })
  it('distinguishes missing data from all low channels and preserves numeric points', () => {
    expect(formatPointValue(null, 2)).toBe('—')
    expect(formatPointValue(0, 2)).toBe('00')
    expect(formatPointValue(23.4)).toBe('23.4')
  })
  it('hides binary floating-point tails in numeric point text', () => {
    expect(formatPointValue(57.910000000000004)).toBe('57.91')
    expect(formatPointValue(0.29000000000000004)).toBe('0.29')
  })
})
