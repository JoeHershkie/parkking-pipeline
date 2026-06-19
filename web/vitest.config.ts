import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    coverage: {
      provider: 'v8',
      include: ['src/lib/**'],
      exclude: [
        'src/lib/places.ts',
        'src/lib/basemap.ts',
        'src/lib/mapStyle.ts',
        'src/lib/schedule/slot.ts',
        'src/lib/schedule/types.ts',
      ],
      thresholds: {
        lines: 65,
        functions: 70,
        branches: 58,
        statements: 65,
      },
    },
  },
})
