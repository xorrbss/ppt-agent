# Electron release dependency audit

## Release policy

The Electron release gate runs `npm run audit:release` from `electron/`. It
executes both of the following audits and parses their JSON output:

- `npm audit --omit=dev --json` is the blocking packaged-runtime gate. Any
  vulnerability at any severity fails the release.
- `npm audit --json` keeps development and build-tool findings visible. The
  difference from the production result is reported separately and does not
  fail a release by itself.

The JSON parser has a fixture-based regression suite:

```bash
cd electron
npm run test:audit-release
npm run audit:release
```

The gate runs before Electron build validation in
`.github/workflows/g4-pptx-roundtrip.yml` and in
`npm run test:local -- --with-electron`.

## Current finding and exposure

As observed on 2026-07-26 with `electron/package-lock.json`, the full audit
reports 16 high-severity vulnerable package entries while the production-only
audit reports zero. The findings are in the development/build chain rooted at
`electron-builder@26.15.3`, primarily through `app-builder-lib`,
`@electron/asar`, `@electron/universal`, `ejs`, `minimatch`, and
`brace-expansion`.

The registry's stable `latest` tag was rechecked on 2026-07-26 and remains
`electron-builder@26.15.3`, which is already locked. The `v26` channel points
to `26.15.7`, while the next major is still an alpha. Neither a channel-only
build nor a prerelease is a bounded security upgrade for release packaging, and
npm's audit remediation still proposes the prohibited 25.x downgrade. No
dependency change was made.

The current package configuration reduces practical exposure:

- Electron Builder is a development dependency and is not a runtime
  dependency of the packaged application.
- `electron/build.js` sets `asar: false`, so the vulnerable ASAR build path is
  not used by the current package.
- Windows packages target NSIS and AppX, not Squirrel.
- Packaging patterns are repository-controlled; the release pipeline does not
  accept untrusted brace or glob expressions.

This makes exploitation in the shipped application unlikely, but does not make
the build-chain findings disappear. A registry outage, malformed audit output,
or a production vulnerability is treated as a gate failure rather than as a
pass.

## Prohibited remediations

Do not use any of these as a shortcut:

- `npm audit fix --force`
- downgrading Electron Builder to npm's suggested 25.x version
- globally overriding `brace-expansion`, `minimatch`, or `@electron/asar`

Those changes cross dependency major versions, can break CommonJS/ESM and glob
API compatibility, and do not provide a verified stable remediation for every
affected path. Dependency versions and overrides must not be changed merely to
make the aggregate audit count disappear.

## Closure criteria and review triggers

The tracked development/build finding can be closed when a stable Electron
Builder release:

1. replaces the vulnerable ASAR, universal, EJS/Jake, minimatch, and
   brace-expansion paths without forced overrides;
2. supports the repository's Node 22 baseline and current NSIS/AppX targets;
3. passes `npm ci`, `npm run test:audit-release`, `npm run audit:release`,
   Electron typecheck/build-config tests, and real package smoke tests on all
   release operating systems.

Re-run the investigation whenever:

- `electron-builder`, `app-builder-lib`, the Electron package targets, or
  `electron/package-lock.json` changes;
- npm publishes or changes an advisory affecting these paths;
- ASAR or Squirrel packaging is enabled;
- the supported Node version changes;
- a release is prepared, or at least quarterly while the finding remains open.
