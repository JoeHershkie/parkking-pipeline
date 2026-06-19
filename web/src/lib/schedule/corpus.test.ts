import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { overlapsMembership } from './membership'
import type { Schedule, Slot } from './types'

const __dirname = dirname(fileURLToPath(import.meta.url))
const CORPUS_PATH = resolve(
  __dirname,
  '../../../../pipeline/tests/fixtures/schedule_corpus.json',
)

interface CorpusCase {
  id: string
  schedule: Schedule
  slot: Slot
  expected: boolean
}

interface Corpus {
  cases: CorpusCase[]
}

function loadCorpus(): CorpusCase[] {
  const raw = readFileSync(CORPUS_PATH, 'utf-8')
  return (JSON.parse(raw) as Corpus).cases
}

describe('schedule corpus parity (Python overlaps_membership)', () => {
  for (const testCase of loadCorpus()) {
    it(testCase.id, () => {
      expect(overlapsMembership(testCase.schedule, testCase.slot)).toBe(
        testCase.expected,
      )
    })
  }
})
