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

Verified unsigned artifacts:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Presenton-0.8.6-beta.exe` | 464,686,365 | `9D5DAB96B4C58FE12EB43886F3C5E3EA02074D963482304041C2AC865CB4A4CA` |
| `Presenton-0.8.6-beta.appx` | 592,399,168 | `EFDC3C3F209F635774C58E8AB4554F0C1A5E5CAE6544E3DE33084AC5C7F33D4F` |

These hashes describe local unsigned candidates only. Rebuild and regenerate
hashes after changing the application version or applying signatures.

## Required release inputs

1. Obtain a trusted Windows code-signing certificate whose subject matches the
   configured AppX publisher, or update the AppX identity and publisher as part
   of a deliberate migration.
2. Add repository Actions secrets:
   - `CSC_LINK`
   - `CSC_KEY_PASSWORD` when the PFX is password protected
3. If the release-upload workflow remains enabled, also configure its R2
   credentials or disable that workflow for the fork. Publishing a release
   without them currently causes the follow-up upload job to fail.

Never record certificate material, passwords, or secret values in this
document, source control, build logs, or uploaded artifacts.

## CI promotion sequence

The local release workflow and every file it references must first exist on the
same remote ref. The workflow alone is insufficient because several verifier,
preflight, and authored-hybrid test files are currently local worktree files.

After an authorized commit and push:

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
| Updated workflow is not on `origin/main` | Remote workflow is still the legacy G4 definition | Authorized commit and push of the workflow and all referenced files |
| No signing inputs | Repository Actions secrets and environments contain no signing secrets | Configure a trusted certificate and secrets |
| AppX signer trust is unknown | Current artifacts are unsigned | Validate certificate subject, chain, timestamp, and runner trust |
| Fork package has not been rebuilt | Existing local artifacts are `0.8.6-beta`; configured version is `2026.7.2401` / AppX `2026.7.2401.0` | Rebuild in CI and verify filenames, manifest version, and hashes |
| AppX identity is upstream-owned | Identity/publisher still use `PresentonAI.Presenton` and the upstream publisher subject | Select a fork identity together with the trusted signing certificate |
| Release upload credentials absent | Repository Actions secrets are empty | Configure R2 credentials or disable the upload workflow |

## Deferred optimization

Keep `asar: false` for the current candidate. FastAPI, Next.js, converter
binaries, Sharp libraries, and Windows Store export caching depend on physical
filesystem paths and working directories. A safe ASAR experiment requires a
tested `resourceRoot` abstraction, external `extraResources`, and a separate
package-output comparison; it is not a release blocker for v0.4.2.
