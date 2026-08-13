import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { extname, resolve } from 'node:path'

import JSZip from 'jszip'

import {
  createBlankPptx,
  insertBlankSlide,
  listMasterParts,
  openPptx,
  replaceAllInDeck,
  savePptx,
} from '../vendor/genoffice-pptx-engine/src/index.ts'

const MACRO_PART = /(?:^|\/)vbaProject\.bin$/i
const EXTERNAL_REL = /\bTargetMode=(?:"External"|'External')/gi
const SMARTART_PART = /^ppt\/diagrams\//i
const OLE_PART = /^ppt\/embeddings\//i
const COMMENT_PART = /^ppt\/(?:comments|commentAuthors|people|threadedComments)(?:\/|\.xml$)/i
const CUSTOM_XML_PART = /^customXml\//i
const TIMING_MARKER = /<p:timing\b/

export class FacadePolicyError extends Error {
  constructor(code, message, evidence = {}) {
    super(message)
    this.name = 'FacadePolicyError'
    this.code = code
    this.evidence = evidence
  }
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

async function writeOpenedBuffered(opened, outputPath) {
  // The engine's streaming writer (`streamFiles: true`) produced a package that
  // passed payload/closure audit but LibreOffice rejected for the existing
  // 90-slide corpus. Keep this PoC on the independently verified buffered path.
  await writeFile(resolve(outputPath), await savePptx(opened))
}

async function inspectPackage(bytes) {
  const zip = await JSZip.loadAsync(bytes)
  const names = Object.keys(zip.files).filter((name) => !zip.files[name].dir).sort()
  const contentTypes = zip.file('[Content_Types].xml')
    ? await zip.file('[Content_Types].xml').async('string')
    : ''
  const relPaths = names.filter((name) => name.endsWith('.rels'))
  let externalRelationships = 0
  const externalRelationshipTypeHashes = new Set()
  for (const relPath of relPaths) {
    const xml = await zip.file(relPath).async('string')
    externalRelationships += [...xml.matchAll(EXTERNAL_REL)].length
    for (const match of xml.matchAll(
      /<Relationship\b[^>]*\bType=(?:"([^"]+)"|'([^']+)')[^>]*\bTargetMode=(?:"External"|'External')[^>]*\/?\s*>/gi,
    )) {
      externalRelationshipTypeHashes.add(sha256(match[1] ?? match[2] ?? ''))
    }
  }
  let animationParts = 0
  for (const name of names.filter((entry) => /^ppt\/slides\/slide\d+\.xml$/i.test(entry))) {
    const xml = await zip.file(name).async('string')
    if (TIMING_MARKER.test(xml)) animationParts += 1
  }
  const macroParts = names.filter((name) => MACRO_PART.test(name))
  return {
    partCount: names.length,
    slideCount: names.filter((name) => /^ppt\/slides\/slide\d+\.xml$/i.test(name)).length,
    macro: {
      detected:
        macroParts.length > 0 ||
        /application\/vnd\.ms-powerpoint\.(?:presentation|template|slideshow)\.macroEnabled/i.test(
          contentTypes,
        ),
      partCount: macroParts.length,
      policy: 'quarantine',
    },
    externalRelationships: {
      detected: externalRelationships > 0,
      count: externalRelationships,
      typeHashes: [...externalRelationshipTypeHashes].sort(),
      policy: 'preserve-inert-no-dereference',
    },
    smartArt: {
      detected: names.some((name) => SMARTART_PART.test(name)),
      partCount: names.filter((name) => SMARTART_PART.test(name)).length,
      policy: 'preserve-pass-through-reject-target-mutation',
    },
    ole: {
      detected: names.some((name) => OLE_PART.test(name)),
      partCount: names.filter((name) => OLE_PART.test(name)).length,
      policy: 'preserve-pass-through-reject-target-mutation',
    },
    animation: {
      detected: animationParts > 0,
      slidePartCount: animationParts,
      policy: 'preserve-pass-through-reject-target-mutation',
    },
    comments: {
      detected: names.some((name) => COMMENT_PART.test(name)),
      partCount: names.filter((name) => COMMENT_PART.test(name)).length,
      policy: 'preserve-pass-through-reject-target-mutation',
    },
    customXml: {
      detected: names.some((name) => CUSTOM_XML_PART.test(name)),
      partCount: names.filter((name) => CUSTOM_XML_PART.test(name)).length,
      policy: 'preserve-pass-through-reject-target-mutation',
    },
  }
}

function findFirstEditableRun(deck) {
  for (let slideIndex = 0; slideIndex < deck.slides.length; slideIndex += 1) {
    const slide = deck.slides[slideIndex]
    for (const element of slide.elements) {
      if (element.type !== 'text' && element.type !== 'shape') continue
      for (const paragraph of element.text?.paragraphs ?? []) {
        for (const run of paragraph.runs ?? []) {
          if (!run.field && run.text?.trim()) {
            return { slideIndex, elementId: element.id, text: run.text }
          }
        }
      }
    }
  }
  return null
}

class EngineSession {
  #opened

  #inputPath

  #features

  constructor(opened, inputPath, features) {
    this.#opened = opened
    this.#inputPath = inputPath
    this.#features = features
  }

  summary() {
    return {
      inputPath: this.#inputPath,
      originalPackageSha256: this.#opened.deck.originalHash,
      slideCount: this.#opened.deck.slides.length,
      sizeEmu: { ...this.#opened.deck.size },
      features: structuredClone(this.#features),
    }
  }

  async #saveBuffered(outputPath) {
    await writeOpenedBuffered(this.#opened, outputPath)
  }

  async saveNoop(outputPath) {
    await this.#saveBuffered(outputPath)
    return { ...this.summary(), outputPath: resolve(outputPath), operation: 'no-op-open-save' }
  }

  async saveTargetedTextEdit(outputPath, marker = ' [GO-PoC]') {
    const target = findFirstEditableRun(this.#opened.deck)
    if (!target) {
      throw new FacadePolicyError('NO_EDITABLE_TEXT', 'No editable text run was found.')
    }
    const replacement = `${target.text}${marker}`
    const changed = replaceAllInDeck(this.#opened.deck, target.text, replacement, {
      matchCase: true,
      firstOnly: true,
      slideIndex: target.slideIndex,
      elementId: target.elementId,
    })
    if (changed.count !== 1 || changed.changedSlides.length !== 1) {
      throw new Error(`Expected one targeted replacement, got ${JSON.stringify(changed)}`)
    }
    await this.#saveBuffered(outputPath)
    return {
      ...this.summary(),
      outputPath: resolve(outputPath),
      operation: 'targeted-text-edit',
      change: {
        replacementCount: changed.count,
        changedSlides: changed.changedSlides,
        elementIdHash: sha256(target.elementId),
        originalTextSha256: sha256(target.text),
        replacementTextSha256: sha256(replacement),
      },
    }
  }

  masterCrudCapabilities() {
    const parts = listMasterParts(this.#opened.archive)
    return {
      create: { status: 'rejected', code: 'UNSUPPORTED_MASTER_TOPOLOGY_MUTATION' },
      read: {
        status: 'supported',
        masters: parts.filter((part) => part.kind === 'master').length,
        layouts: parts.filter((part) => part.kind === 'layout').length,
        partNameHashes: parts.map((part) => sha256(`${part.kind}:${part.partPath}:${part.name}`)),
      },
      update: { status: parts.some((part) => part.kind === 'master') ? 'supported' : 'unavailable' },
      delete: { status: 'rejected', code: 'UNSUPPORTED_MASTER_TOPOLOGY_MUTATION' },
    }
  }

  async saveMasterNameProbe(outputPath, probeName = 'GenOffice PoC Master') {
    const master = listMasterParts(this.#opened.archive).find((part) => part.kind === 'master')
    if (!master) throw new FacadePolicyError('NO_MASTER', 'No slide master part was found.')
    const xml = this.#opened.archive.readText(master.partPath)
    if (!xml) throw new Error(`Master part disappeared: ${master.partPath}`)
    const replacement = /<p:cSld\b[^>]*\bname=(?:"[^"]*"|'[^']*')/.test(xml)
      ? xml.replace(/(<p:cSld\b[^>]*\bname=)(?:"[^"]*"|'[^']*')/, `$1"${probeName}"`)
      : xml.replace('<p:cSld', `<p:cSld name="${probeName}"`)
    if (replacement === xml) throw new Error('Master name probe produced no change.')
    this.#opened.archive.entries.set(master.partPath, Buffer.from(replacement, 'utf8'))
    await this.#saveBuffered(outputPath)
    return {
      ...this.summary(),
      outputPath: resolve(outputPath),
      operation: 'master-update-name-probe',
      masterPart: master.partPath,
      probeNameSha256: sha256(probeName),
      createDeletePolicy: 'reject-unsupported-topology-mutation',
    }
  }
}

export async function openDocument(inputPath, options = {}) {
  const absolute = resolve(inputPath)
  const extension = extname(absolute).toLowerCase()
  if (extension !== '.pptx' && extension !== '.pptm') {
    throw new FacadePolicyError('UNSUPPORTED_EXTENSION', `Expected .pptx or .pptm, got ${extension}.`)
  }
  const bytes = new Uint8Array(await readFile(absolute))
  const features = await inspectPackage(bytes)
  if (features.macro.detected && options.macroPolicy !== 'preserve-unsafe-test-only') {
    throw new FacadePolicyError(
      'QUARANTINE_MACRO_PACKAGE',
      'Macro-enabled packages are quarantined before GenOffice parsing.',
      { macro: features.macro },
    )
  }
  const opened = await openPptx(bytes)
  return new EngineSession(opened, absolute, features)
}

export async function createSyntheticDeck(outputPath, slideCount = 90) {
  if (!Number.isInteger(slideCount) || slideCount < 1 || slideCount > 1000) {
    throw new RangeError('slideCount must be an integer between 1 and 1000.')
  }
  const opened = await openPptx(await createBlankPptx())
  while (opened.deck.slides.length < slideCount) {
    const inserted = insertBlankSlide(opened, opened.deck.slides.length - 1)
    if (!inserted) throw new Error(`Failed to insert synthetic slide ${opened.deck.slides.length + 1}.`)
  }
  await writeOpenedBuffered(opened, outputPath)
  return {
    outputPath: resolve(outputPath),
    operation: 'create-synthetic-deck',
    slideCount: opened.deck.slides.length,
    source: 'GenOffice createBlankPptx + insertBlankSlide',
  }
}

export async function inspectPolicy(inputPath) {
  const bytes = new Uint8Array(await readFile(resolve(inputPath)))
  return inspectPackage(bytes)
}

export async function writeJson(path, value) {
  await writeFile(resolve(path), `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}
