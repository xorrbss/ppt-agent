#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '../../../../..')
const experimentRoot = resolve(here, '..')

function parseArgs(argv) {
  const parsed = {
    upstream: join(repoRoot, '.codex-tmp', 'genoffice-dc4d7e592786'),
    candidate: experimentRoot,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index]
    if (flag !== '--upstream' && flag !== '--candidate') {
      throw new Error(`unknown argument: ${flag}`)
    }
    const value = argv[index + 1]
    if (!value) throw new Error(`missing value for ${flag}`)
    parsed[flag.slice(2)] = resolve(value)
    index += 1
  }
  return parsed
}

const options = parseArgs(process.argv.slice(2))
const policy = JSON.parse(readFileSync(join(here, 'upstream-pin.json'), 'utf8'))
const failures = []
const passes = []

function fail(message) {
  failures.push(message)
}

function pass(message) {
  passes.push(message)
}

function git(cwd, args) {
  const result = spawnSync('git', ['-C', cwd, ...args], {
    encoding: 'utf8',
    windowsHide: true,
  })
  if (result.status !== 0) {
    fail(`git ${args.join(' ')} failed: ${(result.stderr || result.stdout).trim()}`)
    return ''
  }
  return result.stdout.trim()
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function sorted(values) {
  return [...values].sort((left, right) => left.localeCompare(right))
}

function sameSet(actual, expected, label) {
  const left = sorted(actual)
  const right = sorted(expected)
  if (JSON.stringify(left) !== JSON.stringify(right)) {
    fail(`${label} mismatch; actual=${JSON.stringify(left)} expected=${JSON.stringify(right)}`)
    return false
  }
  pass(`${label}: ${left.length} entries`)
  return true
}

function walkFiles(root, visitor, ignored = new Set()) {
  if (!existsSync(root)) {
    fail(`path does not exist: ${root}`)
    return
  }
  const rootReal = realpathSync(root)
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (ignored.has(entry.name)) continue
      const path = join(current, entry.name)
      const stat = lstatSync(path)
      if (stat.isSymbolicLink()) {
        const target = realpathSync(path)
        const rel = relative(rootReal, target)
        if (rel.startsWith(`..${sep}`) || rel === '..' || isAbsolute(rel)) {
          fail(`symlink escapes scan root: ${path} -> ${target}`)
        }
        visitor(path, entry, stat)
      } else if (entry.isDirectory()) {
        visitor(path, entry, stat)
        walk(path)
      } else {
        visitor(path, entry, stat)
      }
    }
  }
  walk(root)
}

function checkPinAndSparseBoundary() {
  if (!existsSync(join(options.upstream, '.git'))) {
    fail(`upstream is not the expected sparse git clone: ${options.upstream}`)
    return
  }

  const head = git(options.upstream, ['rev-parse', 'HEAD'])
  if (head !== policy.expectedCommit) fail(`HEAD mismatch: ${head || '<empty>'}`)
  else pass(`exact commit pin: ${head}`)

  const remote = git(options.upstream, ['remote', 'get-url', 'origin'])
  if (remote !== policy.repository) fail(`origin mismatch: ${remote || '<empty>'}`)
  else pass(`official origin: ${remote}`)

  const dirty = git(options.upstream, ['status', '--short'])
  if (dirty) fail(`pinned upstream worktree is dirty:\n${dirty}`)
  else pass('pinned upstream worktree is clean')

  const sparse = git(options.upstream, ['sparse-checkout', 'list'])
    .split(/\r?\n/)
    .filter(Boolean)
  sameSet(sparse, policy.allowedSparsePaths, 'sparse-checkout allowlist')

  for (const denied of policy.deniedRoots) {
    if (existsSync(join(options.upstream, denied))) fail(`denied upstream root is materialized: ${denied}`)
  }

  const packagesRoot = join(options.upstream, 'packages')
  const materializedPackages = existsSync(packagesRoot)
    ? readdirSync(packagesRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
    : []
  sameSet(materializedPackages, ['pptx-engine'], 'materialized package workspaces')

  const brandedPaths = []
  walkFiles(
    options.upstream,
    (path) => {
      const rel = relative(options.upstream, path).split(sep).join('/')
      const segments = rel.toLowerCase().split('/')
      const base = segments.at(-1)
      if (
        segments.some((segment) => segment === 'branding' || segment === 'brand-assets') ||
        /^(logo|brand)(\.|-|_)/i.test(base)
      ) {
        brandedPaths.push(rel)
      }
    },
    new Set(['.git', 'node_modules']),
  )
  if (brandedPaths.length) fail(`branding assets are materialized: ${brandedPaths.join(', ')}`)
  else pass('no branding asset paths materialized')
}

function checkEngineBoundary() {
  const engineRoot = join(options.upstream, policy.package.path)
  const manifestPath = join(engineRoot, 'package.json')
  if (!existsSync(manifestPath)) {
    fail(`missing engine manifest: ${manifestPath}`)
    return
  }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  for (const key of ['name', 'version', 'license']) {
    if (manifest[key] !== policy.package[key]) {
      fail(`engine package ${key} mismatch: ${JSON.stringify(manifest[key])}`)
    }
  }
  if (manifest.private !== true) fail('engine package must remain private in this PoC intake')

  const runtimeNames = Object.keys(manifest.dependencies || {})
  sameSet(runtimeNames, policy.allowedRuntimeDependencies, 'engine runtime dependencies')
  for (const section of ['dependencies', 'optionalDependencies', 'peerDependencies']) {
    for (const name of Object.keys(manifest[section] || {})) {
      if (name.startsWith('@genoffice/')) fail(`workspace runtime dependency is denied: ${section}.${name}`)
    }
  }

  const sourceRoot = join(engineRoot, 'src')
  const bareImports = new Set()
  const outsideRelativeImports = []
  walkFiles(sourceRoot, (path, entry) => {
    if (entry.isDirectory() || !/\.[cm]?[jt]sx?$/.test(entry.name)) return
    const source = readFileSync(path, 'utf8')
    const importPattern = /(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*)['"]([^'"]+)['"]/g
    for (const match of source.matchAll(importPattern)) {
      const specifier = match[1]
      if (specifier.startsWith('.')) {
        const target = resolve(dirname(path), specifier)
        const rel = relative(engineRoot, target)
        if (rel.startsWith(`..${sep}`) || rel === '..' || isAbsolute(rel)) {
          outsideRelativeImports.push(`${relative(engineRoot, path)} -> ${specifier}`)
        }
      } else if (!specifier.startsWith('node:')) {
        const packageName = specifier.startsWith('@')
          ? specifier.split('/').slice(0, 2).join('/')
          : specifier.split('/')[0]
        bareImports.add(packageName)
      }
    }
  })
  sameSet(bareImports, policy.allowedRuntimeDependencies, 'engine source bare imports')
  if (outsideRelativeImports.length) fail(`relative imports escape engine root: ${outsideRelativeImports.join(', ')}`)
  else pass('engine relative imports stay inside packages/pptx-engine')
}

function productionClosure(lock, rootNames) {
  const closure = new Map()
  const edges = []
  const visit = (name) => {
    if (closure.has(name)) return
    const entry = lock.packages?.[`node_modules/${name}`]
    if (!entry?.version) throw new Error(`package-lock lacks runtime entry for ${name}`)
    closure.set(name, entry)
    for (const child of Object.keys(entry.dependencies || {})) {
      edges.push([name, child])
      visit(child)
    }
  }
  for (const name of rootNames) visit(name)
  return { closure, edges }
}

function spdxId(name) {
  return `SPDXRef-Package-${name.replace(/^@/, '').replace(/[^A-Za-z0-9.-]+/g, '-')}`
}

function checkLicenseCopiesAndSbom() {
  for (const [artifact, expectedHash] of Object.entries(policy.artifactHashes)) {
    const artifactPath = join(here, artifact)
    const upstreamName = artifact === 'LICENSE.genoffice.txt' ? 'LICENSE' : 'NOTICE'
    const upstreamPath = join(options.upstream, upstreamName)
    if (!existsSync(artifactPath) || !existsSync(upstreamPath)) {
      fail(`license artifact or upstream source is missing: ${artifact}`)
      continue
    }
    const artifactHash = sha256(artifactPath)
    const upstreamHash = sha256(upstreamPath)
    if (artifactHash !== expectedHash || upstreamHash !== expectedHash) {
      fail(`${artifact} is not an exact pinned copy: artifact=${artifactHash} upstream=${upstreamHash}`)
    } else {
      pass(`${artifact} exact-copy SHA-256: ${artifactHash}`)
    }
  }

  const lock = JSON.parse(readFileSync(join(options.upstream, 'package-lock.json'), 'utf8'))
  const engineLock = lock.packages?.[policy.package.path]
  if (!engineLock) {
    fail(`package-lock lacks ${policy.package.path}`)
    return
  }
  let runtime
  try {
    runtime = productionClosure(lock, Object.keys(engineLock.dependencies || {}))
  } catch (error) {
    fail(error.message)
    return
  }

  const sbomPath = join(here, 'sbom.spdx.json')
  const sbom = JSON.parse(readFileSync(sbomPath, 'utf8'))
  if (sbom.spdxVersion !== 'SPDX-2.3') fail(`unexpected SPDX version: ${sbom.spdxVersion}`)
  const expectedNamespace = `https://github.com/genspark-ai/genoffice/tree/${policy.expectedCommit}/sbom/pptx-engine-${policy.expectedCommit}`
  if (sbom.documentNamespace !== expectedNamespace) fail('SBOM namespace does not bind the exact commit')

  const expectedPackages = new Map([[policy.package.name, policy.package.version]])
  for (const [name, entry] of runtime.closure) expectedPackages.set(name, entry.version)
  const actualPackages = new Map((sbom.packages || []).map((item) => [item.name, item]))
  sameSet(actualPackages.keys(), expectedPackages.keys(), 'SBOM production package inventory')
  for (const [name, version] of expectedPackages) {
    const item = actualPackages.get(name)
    if (!item) continue
    if (item.versionInfo !== version) fail(`SBOM version mismatch for ${name}: ${item.versionInfo} != ${version}`)
    if (item.SPDXID !== spdxId(name)) fail(`SBOM SPDXID mismatch for ${name}: ${item.SPDXID}`)
    if (name !== policy.package.name) {
      const lockEntry = runtime.closure.get(name)
      if (item.downloadLocation !== lockEntry.resolved) fail(`SBOM resolved URL mismatch for ${name}`)
      if (item.licenseDeclared !== (lockEntry.license || 'NOASSERTION')) {
        fail(`SBOM declared license mismatch for ${name}`)
      }
    }
  }

  const expectedRelationships = new Set([
    `SPDXRef-DOCUMENT|DESCRIBES|${spdxId(policy.package.name)}`,
    ...Object.keys(engineLock.dependencies || {}).map(
      (name) => `${spdxId(policy.package.name)}|DEPENDS_ON|${spdxId(name)}`,
    ),
    ...runtime.edges.map(([from, to]) => `${spdxId(from)}|DEPENDS_ON|${spdxId(to)}`),
  ])
  const actualRelationships = new Set(
    (sbom.relationships || []).map(
      (item) => `${item.spdxElementId}|${item.relationshipType}|${item.relatedSpdxElement}`,
    ),
  )
  sameSet(actualRelationships, expectedRelationships, 'SBOM production dependency relationships')
}

function checkCandidateFailClosed() {
  const forbiddenSegments = new Set(['apps', 'ee', 'branding', 'brand-assets'])
  const forbiddenPaths = []
  const manifests = []
  walkFiles(
    options.candidate,
    (path, entry) => {
      const rel = relative(options.candidate, path).split(sep).join('/')
      const segments = rel.toLowerCase().split('/')
      const base = segments.at(-1)
      if (
        segments.some((segment) => forbiddenSegments.has(segment)) ||
        /^(logo|brand)(\.|-|_)/i.test(base) ||
        base.endsWith('.asar')
      ) {
        forbiddenPaths.push(rel)
      }
      if (!entry.isDirectory() && entry.name === 'package.json') manifests.push(path)
    },
    new Set(['.git', 'node_modules', 'artifacts']),
  )
  if (forbiddenPaths.length) fail(`candidate contains denied app/ee/branding paths: ${forbiddenPaths.join(', ')}`)
  else pass('candidate has no app/ee/branding paths')

  const dependencyFailures = []
  const engineIntakes = []
  for (const manifestPath of manifests) {
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    if (manifest.name === policy.package.name) engineIntakes.push(dirname(manifestPath))
    else if (manifest.name === 'genoffice' || String(manifest.name || '').startsWith('@genoffice/')) {
      dependencyFailures.push(`${relative(options.candidate, manifestPath)}: denied package identity ${manifest.name}`)
    }
    for (const section of ['dependencies', 'optionalDependencies', 'peerDependencies', 'devDependencies']) {
      for (const [name, spec] of Object.entries(manifest[section] || {})) {
        const otherWorkspace = name.startsWith('@genoffice/') && name !== '@genoffice/pptx-engine'
        const forbiddenProduct = /(^|[/_-])(electron|agent-core|ai-provider|openai|anthropic)([/_-]|$)/i.test(name)
        const forbiddenPathSpec = /(^|[\\/])(apps|ee)([\\/]|$)|packages[\\/](?!pptx-engine(?:[\\/]|$))/i.test(String(spec))
        if (otherWorkspace || forbiddenProduct || forbiddenPathSpec) {
          dependencyFailures.push(
            `${relative(options.candidate, manifestPath)}: ${section}.${name}=${spec}`,
          )
        }
      }
    }
  }
  if (dependencyFailures.length) fail(`candidate dependency boundary violations: ${dependencyFailures.join('; ')}`)
  else pass(`candidate package manifests are fail-closed: ${manifests.length} checked`)

  if (engineIntakes.length !== 1) {
    fail(`candidate must contain exactly one ${policy.package.name} intake; found ${engineIntakes.length}`)
    return
  }

  const expectedRoot = join(options.upstream, policy.package.path)
  const candidateRoot = engineIntakes[0]
  const expectedFiles = []
  const candidateFiles = []
  walkFiles(expectedRoot, (path, entry) => {
    if (entry.isDirectory()) return
    const rel = relative(expectedRoot, path).split(sep).join('/')
    if (rel === 'package.json' || rel === 'tsconfig.json' || rel.startsWith('src/')) expectedFiles.push(rel)
  })
  walkFiles(
    candidateRoot,
    (path, entry) => {
      if (!entry.isDirectory()) candidateFiles.push(relative(candidateRoot, path).split(sep).join('/'))
    },
    new Set(['node_modules']),
  )
  if (!sameSet(candidateFiles, expectedFiles, 'candidate pinned engine file inventory')) return

  const changed = expectedFiles.filter(
    (rel) => sha256(join(expectedRoot, ...rel.split('/'))) !== sha256(join(candidateRoot, ...rel.split('/'))),
  )
  if (changed.length) fail(`candidate engine files differ from exact pin: ${changed.join(', ')}`)
  else pass(`candidate ${policy.package.name} bytes match exact pin: ${expectedFiles.length} files`)
}

checkPinAndSparseBoundary()
checkEngineBoundary()
checkLicenseCopiesAndSbom()
checkCandidateFailClosed()

for (const message of passes) console.log(`PASS ${message}`)
if (failures.length) {
  for (const message of failures) console.error(`FAIL ${message}`)
  console.error(`RESULT FAIL (${failures.length} violation${failures.length === 1 ? '' : 's'})`)
  process.exitCode = 1
} else {
  console.log(`RESULT PASS (${passes.length} checks)`)
}
