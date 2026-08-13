import assert from 'node:assert/strict'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import JSZip from 'jszip'

import {
  FacadePolicyError,
  createSyntheticDeck,
  inspectPolicy,
  openDocument,
} from '../src/genoffice-facade.mjs'

test('synthetic deck no-op preserves slide count behind the facade', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'genoffice-facade-'))
  const source = join(dir, 'synthetic-3.pptx')
  const output = join(dir, 'synthetic-3-noop.pptx')
  await createSyntheticDeck(source, 3)
  const session = await openDocument(source)
  assert.equal(session.summary().slideCount, 3)
  assert.equal('archive' in session, false)
  await session.saveNoop(output)
  const reopened = await openDocument(output)
  assert.equal(reopened.summary().slideCount, 3)
})

test('macro package is quarantined before engine parsing', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'genoffice-macro-'))
  const base = join(dir, 'base.pptx')
  const macro = join(dir, 'macro.pptm')
  await createSyntheticDeck(base, 1)
  const zip = await JSZip.loadAsync(await readFile(base))
  zip.file('ppt/vbaProject.bin', Buffer.from([0, 1, 2, 3]))
  await writeMacroFixture(zip, macro)
  const policy = await inspectPolicy(macro)
  assert.equal(policy.macro.detected, true)
  await assert.rejects(openDocument(macro), (error) => {
    assert.ok(error instanceof FacadePolicyError)
    assert.equal(error.code, 'QUARANTINE_MACRO_PACKAGE')
    return true
  })
})

async function writeMacroFixture(zip, path) {
  const { writeFile } = await import('node:fs/promises')
  await writeFile(path, await zip.generateAsync({ type: 'nodebuffer' }))
}
