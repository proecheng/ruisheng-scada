import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('PWA cache policy', () => {
  it('does not cache authenticated APIs or reference missing icons', () => {
    const viteConfig = readFileSync(resolve(process.cwd(), 'vite.config.ts'), 'utf8')

    expect(viteConfig).not.toContain('runtimeCaching')
    expect(viteConfig).not.toContain('api-cache')
    expect(viteConfig).not.toContain('pwa-192.png')
    expect(viteConfig).not.toContain('pwa-512.png')
  })
})
