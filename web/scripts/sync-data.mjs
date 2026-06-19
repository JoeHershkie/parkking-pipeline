import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const source = resolve(webRoot, '../pipeline/data/final_parking_map.geojson')
const destDir = resolve(webRoot, 'public/data')
const dest = resolve(destDir, 'final_parking_map.geojson')

if (!existsSync(source)) {
  console.warn(
    `sync-data: skip — ${source} not found (run parking-geo in pipeline/ first)`,
  )
  process.exit(0)
}

mkdirSync(destDir, { recursive: true })
copyFileSync(source, dest)
console.log(`sync-data: copied pipeline output to ${dest}`)
