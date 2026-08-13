import { readFile } from 'node:fs/promises'

import ts from 'typescript'

/**
 * GenOffice's source package uses bundler-style extensionless TypeScript imports.
 * Keep the exact pinned source unchanged and adapt relative resolution/transpilation.
 */
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context)
  } catch (error) {
    const relative = specifier.startsWith('./') || specifier.startsWith('../')
    const extensionless = !/[./\\][^/\\]+\.[a-z0-9]+$/i.test(specifier)
    if (!relative || !extensionless || error?.code !== 'ERR_MODULE_NOT_FOUND') throw error
    return nextResolve(`${specifier}.ts`, context)
  }
}

export async function load(url, context, nextLoad) {
  if (!url.endsWith('.ts')) return nextLoad(url, context)
  const source = await readFile(new URL(url), 'utf8')
  const output = ts.transpileModule(source, {
    fileName: new URL(url).pathname,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      isolatedModules: true,
      sourceMap: false,
    },
  })
  return { format: 'module', shortCircuit: true, source: output.outputText }
}
