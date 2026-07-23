# Windows v0.4.2 release readiness

Status date: 2026-07-24

## Decision

The v0.4.2 export runtime is ready for an unsigned Windows release-candidate
gate. The fork-specific application version is `2026.7.2401`, its AppX version
is `2026.7.2401.0`, and update metadata targets `xorrbss/ppt-agent`. Public
distribution remains on hold until a trusted code-signing certificate and
fork-owned AppX identity/publisher are configured.

Do not publish the previously built `0.8.6-beta` artifacts as a fork release.
They predate the fork version and update-channel separation documented here.

## Verified locally

- Root and Electron export pins both resolve to `v0.4.2`.
- Authored-hybrid regression suite passes, including the Chrome-backed
  editable-export fixture.
- Windows package structure verification passes for NSIS, AppX, Next.js,
  FastAPI, and the bundled export marker.
- Packaged Electron and Next.js Sharp runtimes load with Sharp `0.34.5` and
  libvips `8.17.3`.
- The unsigned NSIS installer completes a silent installation into an isolated
  directory, contains export marker `v0.4.2`, and silently uninstalls without
  leaving `Presenton.exe` or the installation directory behind.
- The strict signing gate correctly rejects the current NSIS, AppX, and
  unpacked executable as `NotSigned`.

## Verified in CI

The manual `G4 round-trip and release gates` run
[`30026316385`](https://github.com/xorrbss/ppt-agent/actions/runs/30026316385)
completed successfully at commit
`68b6e7e04f41d75ac81d0226ef8d4dfbe2f073d2`.

- Production export-runtime staging passed.
- Adaptive PPTX round-trip and legacy export smoke passed.
- The Windows v0.4.2 release gate passed.
- NSIS and AppX packaging passed.
- The packaged Sharp runtime passed its load check.
- The NSIS installer passed isolated unattended install and uninstall.
- Unsigned AppX installation was intentionally skipped; its package structure
  and manifest were verified.

Verified unsigned CI artifacts:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Presenton-2026.7.2401.exe` | 417,346,068 | `A89EC72EE136EDF7F220EEB45AFEBF329B86314E623296F76F6E3503BA9165F6` |
| `Presenton-2026.7.2401.appx` | 541,183,655 | `406C2BC8FBC48B2814ABFC3789B6A159881651C345E141A149FB2DCEEBE05661` |

These hashes describe the unsigned CI candidate only. The uploaded artifact is
`Presenton-Windows-68b6e7e04f41d75ac81d0226ef8d4dfbe2f073d2`
(artifact ID `8571912061`, scheduled to expire on 2026-08-06). Rebuild and
regenerate hashes after changing the application version, identity, publisher,
or signatures.

The older local `Presenton-0.8.6-beta` artifacts are obsolete and the current
verifier correctly rejects their AppX version. They must not be promoted.

## Required release inputs

1. Obtain a trusted Windows code-signing certificate whose subject matches the
   configured AppX publisher, or update the AppX identity and publisher as part
   of a deliberate migration.
2. Add repository Actions secrets:
   - `CSC_LINK`
   - `CSC_KEY_PASSWORD` when the PFX is password protected
3. If the release-upload workflow remains enabled, also configure its R2
   environment and credentials:
   - environment: `sync_r2`
   - `R2_ACCOUNT_ID`
   - `R2_ACCESS_KEY`
   - `R2_SECRET_KEY`

   Confirm that the `presenton-desktop` bucket exists and that these credentials
   can write to `presenton-desktop/${VERSION}`. Publishing a release without
   them causes the follow-up upload job to fail or wait.

Never record certificate material, passwords, or secret values in this
document, source control, build logs, or uploaded artifacts.

## CI promotion sequence

The unsigned sequence through step 2 has completed on `origin/main`. For a
signed release:

1. Run `G4 round-trip and release gates` with:
   - `package_windows=true`
   - `require_signed=false`
2. Require the structural verifier and silent NSIS install/uninstall smoke to
   pass. Unsigned AppX installation is intentionally skipped.
3. Configure the trusted signing inputs and rerun with:
   - `package_windows=true`
   - `require_signed=true`
4. Require valid Authenticode for NSIS, AppX, and unpacked `Presenton.exe`,
   presence of `AppxSignature.p7x`, and successful AppX
   install/identity/remove smoke.
5. Download the CI-produced artifacts, verify `SHA256SUMS.windows.txt`, and
   retain the workflow run URL with the release record.

## Promotion blockers

| Blocker | Current evidence | Resolution |
| --- | --- | --- |
| No signing inputs | Repository Actions secrets and environments contain no signing secrets | Configure a trusted certificate, `CSC_LINK`, and optional `CSC_KEY_PASSWORD` |
| Fork identity and publisher are undecided | Build configuration still uses `PresentonAI.Presenton`, `Presenton Inc.`, and the upstream publisher subject | Select fork-owned identifiers and use the exact signing-certificate subject as AppX publisher |
| AppX signer trust is unknown | The verified CI artifacts are unsigned | Validate certificate EKU, expiry, chain, subject, timestamp, and runner trust |
| Release upload environment and credentials are absent | Repository environments, Actions secrets, and releases are empty | Create `sync_r2`, configure the three R2 secrets, and verify bucket access before publishing |

## Deferred optimization

Keep `asar: false` for the current candidate. FastAPI, Next.js, converter
binaries, Sharp libraries, and Windows Store export caching depend on physical
filesystem paths and working directories. A safe ASAR experiment requires a
tested `resourceRoot` abstraction, external `extraResources`, and a separate
package-output comparison; it is not a release blocker for v0.4.2.
