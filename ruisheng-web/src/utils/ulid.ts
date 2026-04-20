import { ulid, monotonicFactory } from 'ulid'

const monotonic = monotonicFactory()

export function generateUlid(): string {
  return monotonic()
}

export { ulid }
