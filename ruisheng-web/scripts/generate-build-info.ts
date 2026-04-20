import { execSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..')
const out = resolve(root, 'public', 'build-info.json')

let hash = 'dev'
try {
  hash = execSync('git rev-parse --short HEAD', { cwd: root }).toString().trim()
} catch {
  /* ignore */
}

const info = {
  build_hash: hash,
  build_time: new Date().toISOString(),
  node_version: process.version,
}

mkdirSync(resolve(root, 'public'), { recursive: true })
writeFileSync(out, JSON.stringify(info, null, 2))
console.log(`build-info.json → ${JSON.stringify(info)}`)
