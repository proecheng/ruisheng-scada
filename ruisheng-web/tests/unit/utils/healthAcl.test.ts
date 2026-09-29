import { describe, expect, it } from 'vitest'
import { isExpectedHealthAclConsoleError } from '../../../e2e/fixtures/healthAcl'

const forbidden = 'Failed to load resource: the server responded with a status of 403 (Forbidden)'
const page = 'http://localhost:5173/__diag'

describe('health ACL console classification', () => {
  it('accepts only the expected same-origin health response', () => {
    expect(isExpectedHealthAclConsoleError(forbidden, 'http://localhost:5173/api/health/ready', page)).toBe(true)
  })

  it.each([
    'http://localhost:5173/api/devices',
    'http://localhost:5173/api/health/ready?unexpected=1',
    'http://localhost:5173/api/health/ready#unexpected',
    'http://localhost:8000/api/health/ready',
    'https://example.com/api/health/ready',
    '',
  ])('does not hide a denial from %s', (url) => {
    expect(isExpectedHealthAclConsoleError(forbidden, url, page)).toBe(false)
  })

  it('does not hide server errors or application console messages', () => {
    const url = 'http://localhost:5173/api/health/ready'
    expect(isExpectedHealthAclConsoleError(forbidden.replace('403 (Forbidden)', '500 (Internal Server Error)'), url, page)).toBe(false)
    expect(isExpectedHealthAclConsoleError('unexpected error', url, page)).toBe(false)
  })
})
