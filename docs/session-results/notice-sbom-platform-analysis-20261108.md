# NOTICE / release-SBOM platform-dependence analysis

Date: 2026-11-08. Repo: /home/user/ppt-agent (HEAD tree untouched; `git status` clean after the investigation).
Linux machine: uv-synced `servers/fastapi/.venv` (default dev group) and `npm ci` trees.
Scratch artefacts: `scratchpad/mirror/` (copy of the scripts + compliance files, symlinks to the real trees; contains a regenerated NOTICE as `NOTICE.linux`, the policy copy edited for experiments, and a regenerated SBOM), `scratchpad/cmp.py`, `c.txt`/`m.txt` header lists.

Legend: [V] verified by running or reading something here, [U] unverified / inferred.

## 0. Headline findings

1. The failure is NOT only platform dependence. The committed NOTICE and committed SBOM are also stale against the current lockfiles, independent of OS [V]. Making the checks "platform neutral" will not turn them green without a regeneration.
2. The platform-dependent part of NOTICE is small on the Python side (Pillow, numpy, plus a handful of packages present on only one OS) [V].
3. On the Node side, the committed NOTICE contains about 130 distinct packages that are in neither `package-lock.json` nor `servers/nextjs/package-lock.json` (tiptap, prosemirror, mermaid, d3, recharts, chart.js, html2canvas, cytoscape, ...) [V]. `rebuild_notice_all.py` scans whatever is in `node_modules`, not the lockfiles. So the committed NOTICE was generated from a stale Windows `node_modules` that still held packages the repo had dropped. A fresh `npm ci` on Windows would not reproduce it either [U for the Windows run, V that the packages are absent from both lockfiles].
4. `check:release-sbom` has more Linux failures than the `caio` digest [V, see 2.2]. It also embeds `sha256(NOTICE)` and `sha256(policy.json)` in the SBOM, so it is coupled to NOTICE bytes.
5. In CI, the step that runs `test:next-ci` installs neither python nor uv [V from bitbucket-pipelines.yml]. Both checks shell out to `uv`. So that step is probably red for a reason that precedes any platform issue [U: whether the `cypress/included:15.19.0` image happens to ship uv is not verified].

## 1. Which packages make NOTICE output platform dependent

Method: ran `scripts/rebuild_notice_all.py` from a scratch copy (REPO_ROOT derives from the script location, so the real NOTICE was not touched); got 47,046 lines vs 52,705 committed. Compared per-package blocks with `cmp.py` by importing the script's own scan functions.

### 1.1 Python (200 installed distributions; 9 blocks differ from committed)

| Package | Class | What differs |
|---|---|---|
| pillow 12.3.0 | platform wheel | `dist-info/licenses/LICENSE` is assembled per wheel. Windows text has `===== brotli-1.2.0 =====`, `freetype-2.14.3`, `harfbuzz-14.2.1`, `lcms2`, `libavif`, `libjpeg-turbo`, `libpng`, `libwebp`, `openjpeg`, `tiff`, `xz`, `zlib-ng`. The manylinux text has a different layout and set (AOM, BROTLI, BZIP2, DAV1D, FREETYPE2, HARFBUZZ, LCMS2, LIBAVIF, ...). 753 vs 609 lines. |
| numpy 2.4.4 | platform wheel | Bundled-library section lists `numpy.libs\libscipy_openblas*.dll` on Windows; `libscipy_openblas*.so` and `libgfortran*.so` on Linux. |
| coverage 7.13.5 | wheel METADATA | Only the author line: "Ned Batchelder and 259 others" (committed) vs "253 others" (Linux). Cause unverified; possibly a different wheel build. |
| execnet 2.1.2, pytest-xdist 3.8.0 | whitespace | Committed block lacks one trailing blank line that the Linux run emits. Both are dev-group packages. Cause not determined [U]; may be script/version drift rather than OS. |
| jeepney 0.9.0, SecretStorage 3.5.0, uvloop 0.22.1 | installed on Linux only (uv markers) | Not in committed NOTICE. |
| regex 2026.9.29 | stale | Committed has 2026.4.4. `uv.lock` has 2026.9.29 (`07e794ab5`, 2026-11-02). Not platform related. |
| colorama 0.4.6, pywin32 311, pywin32-ctypes 0.2.3, win32-setctime 1.2.0, tzdata 2026.2 | installed on Windows only | In committed NOTICE, absent on Linux. tzdata is `marker = sys_platform == 'win32'` in uv.lock. |
| joblib 1.5.3, nltk 3.10.0 | in committed only | Not in `uv.lock` [V by grep for tzdata only; joblib/nltk are not installed here; whether the lock still has them was not checked, so this is U]. |

Because NOTICE is built from installed dist-info, the output also depends on which dependency groups were synced (the default `uv sync` includes dev: coverage, execnet, pytest-xdist are in NOTICE) [V].

### 1.2 Node (892 entries scanned; committed has 1,054 name@version headers vs 862 regenerated)

- Platform packages: `@esbuild/win32-x64`, `@img/sharp-win32-x64`, `@next/swc-win32-x64-msvc`, `@parcel/watcher-win32-x64`, `@swc/core-win32-x64-msvc`, `@unrs/resolver-binding-win32-x64-msvc` (committed) vs the linux-x64 equivalents; plus Linux-only `@emnapi/runtime`, `@img/sharp-libvips-linux-x64`, `@img/sharp-wasm32`, and a nested `@swc/helpers`. [V]
- Stale content, platform independent: about 130 packages listed in item 3 of the headline. [V]
- NOTICE scans `node_modules` for both the root and `servers/nextjs`, including dev dependencies. [V from script]

Net: roughly 8k changed lines come from (a) stale Node entries, (b) per-OS binary packages, (c) Pillow/numpy bundled-library text, (d) Linux-only or Windows-only Python distributions. The user's "17k-line diff" count was not reproduced exactly (I count 8,159 changed lines with plain `diff`; the discrepancy is probably diff granularity) [U].

## 2. CI and the SBOM check

### 2.1 How CI runs them

`bitbucket-pipelines.yml`, step `nextjs-ci-node-contracts` (image `cypress/included:15.19.0`, line 1; Debian-based Linux [U]): `npm ci`, `npm ci --prefix servers/nextjs`, `npm run test:next-ci` (lines 11-19). Runs on all PRs and `main`.

`test:next-ci` = `... && npm run check:release-sbom && npm run check:notice && ...` (package.json line 19). Both checks added in the 2026-10-26 merge `25bce4da7`.

- `check:release-sbom` calls `spawnSync("uv", ["run","--frozen","python",...])` and `uv export` (release-license-policy.mjs `loadPythonPackageMetadata`, generate-release-sbom.mjs `pythonPackages`).
- `check:notice` is `uv run --directory servers/fastapi python ...rebuild_notice_all.py --check`.
- The step never installs python, uv or runs `uv sync`. The other Python steps in the file do (`python3 -m venv .bitbucket-uv`, `pip install uv==0.11.17`, `uv sync --frozen`).
- `docs/session-results/codex-review-followup-20261108.md` says the NOTICE check and the `caio` digest check "also fail here and before Codex". `artifacts/verbatim-design-impl-20260911/ci-coverage-gap.md` Q2.E already flagged that this step invokes uv in an image with no Python, and could not query Bitbucket history.

Conclusion: CI cannot pass today. Even with uv present it would fail at the first Linux assertion (caio), and after that on NOTICE. Real Bitbucket build history was not accessible [U], so "red on every build since 10-26" is inferred.

### 2.2 SBOM failures on Linux (beyond caio)

Reproduced in the scratch mirror. `exactMetadataResolutions` pins one licence-file SHA-256 per package (`exactResolution`, release-license-policy.mjs:85-95), and the assertion fires if the installed file hash differs.

| Package | Policy digest | Linux installed | Where each digest comes from [V by downloading from PyPI] |
|---|---|---|---|
| caio 0.9.25 | 07653d... = `py3-none-any` wheel COPYING | c79de2... | Windows has no native caio wheel in `uv.lock`, so uv installs the pure-Python wheel. The sdist and all three native manylinux wheels carry c79de2... |
| py-rust-stemmers 0.1.5 | 944905... = the `win_amd64` wheel LICENSE | f79ad9... | The sdist and every non-Windows wheel carry f79ad9...; only the Windows wheel differs. |
| docutils 0.22.4 | matches Linux | - | OK |

Then `resolvePythonLicense` throws `installed license metadata is missing` for packages that are in `uv export` but not installed on Linux: colorama 0.4.6, pywin32 311, pywin32-ctypes 0.2.3, tzdata 2026.2, win32-setctime 1.2.0. The policy's `exactPlatformMetadataResolutions` covers only the reverse direction (jeepney, secretstorage, uvloop: Linux-only packages absent on Windows). It was authored one-sidedly from Windows. [V]

Experiment in scratch: edited a copy of the policy to use the Linux digests and add the 5 Windows-only packages as platform resolutions. The generator then ran to completion. Remaining differences against the committed SBOM [V]:
- committed SBOM has 597 packages, regenerated 600: missing `defusedxml 0.7.1`, `regex 2026.9.29` (the lock changed on 11-02), and a nested `next-intl/node_modules/@swc/helpers` (lock fix `aa4b12439`, 11-02). The committed SBOM was last written 10-26. This staleness is platform independent and would fail on Windows too.
- the SPDX ids carry an index (`SPDXRef-python-<name>-<n>-<ver>`), so one insertion shifts every later id.
- `policySha256`, `noticeSha256`, `documentNamespace` all change because the policy and NOTICE bytes changed.

So a digest fix alone leaves `check:release-sbom` red; the SBOM must be regenerated after the final NOTICE/policy bytes exist, in that order.

## 3. Options

Constraint from the task: do not regenerate/commit NOTICE or change policy now. These are proposals only.

### Option A. Treat Linux (the CI image) as the canonical generation platform
Regenerate NOTICE and SBOM on the CI Linux image (or a pinned container) only; Windows developers do not run the check/generate.
- Files: `NOTICE`, `compliance/release-sbom.spdx.json`, `compliance/release-license-policy.json` (swap the two digests to c79de2.../f79ad9..., add the 5 Windows-only packages to `exactPlatformMetadataResolutions`, or drop the Windows-only markers from the export), `bitbucket-pipelines.yml` (install python3/venv/uv and `uv sync --frozen` in `nextjs-ci-node-contracts`), plus the contract test `scripts/required-ci-contract.test.mjs` if the step text is asserted (it asserts the package.json script strings, lines 158-166).
- Pros: smallest change; CI becomes deterministic; shipped Docker image is Linux, so the NOTICE then describes what is actually shipped.
- Cons: the Windows-native run (CLAUDE.md "Windows native") then ships a NOTICE that does not list Windows-only wheels (pywin32, colorama, win32-setctime, Windows OpenBLAS text); `generate:notice` on Windows would produce a dirty diff. Need a documented rule that Windows must not regenerate. Also still pins the dev group choice and the pip wheel selection (e.g. a different glibc/arch wheel for aarch64 would change Pillow text; [U] whether CI is ever arm).

### Option B. Make NOTICE a union, and platform-specific text a separate appendix
Change `rebuild_notice_all.py` to (1) scan the lockfiles (`uv.lock`, both `package-lock.json`) rather than installed trees, so the package list is platform- and cache-independent; (2) skip per-wheel bundled third-party sections (Pillow `LICENSE`, numpy `Files:` blocks) in the main body, emit only the package's own licence file; (3) emit those wheel-bundled texts into `NOTICE.platform-<tag>.txt` or a keyed appendix, checked per platform.
- Files: `scripts/rebuild_notice_all.py` (substantial), `NOTICE`, new appendix file(s) or a `compliance/notice-platform/` dir, `scripts/release-license-policy.mjs` (`noticeSha256` currently hashes one file), `compliance/release-license-policy.json`, `scripts/generate-release-sbom.mjs`, `scripts/required-ci-contract.test.mjs` (hard-codes the script command lines), `package.json` scripts if names change.
- Pros: root cause fixed; the main NOTICE is OS independent; a Windows and a Linux developer get the same result. Also fixes the stale-node_modules problem (headline 3), which neither A nor C touches.
- Cons: the biggest change; a compliance reviewer must accept that bundled native licences move to appendices (they must still be shipped with the matching binary; for the Docker image that means the Linux appendix). Requires the licence-review owner's decision. Lockfiles have no licence text, so text still has to be read from installed packages or fetched, which would need a fallback for packages missing on the current OS (colorama/pywin32 on Linux) [U how to source those texts offline; the PyPI sdists carry them, as verified for caio/py-rust-stemmers].

### Option C. Accept a reviewed set of digests per package (and per platform) in the policy
Change `exactMetadataResolutions` from one `licenseFileSha256` to an array (or `{platform: digest}` map), and `exactResolution` to accept any listed digest. Extend `exactPlatformMetadataResolutions` to cover Windows-only packages (colorama, pywin32, pywin32-ctypes, tzdata, win32-setctime).
- Files: `compliance/release-license-policy.json`, `scripts/release-license-policy.mjs` (`exactResolution`, lines 85-95), `scripts/release-license-policy.test.mjs` (add cases), then regenerated `compliance/release-sbom.spdx.json`.
- Pros: small; keeps the "reviewed file changed" tripwire; fixes the SBOM side on both OSes. Evidence: the digests per platform are already verified above (caio: 07653d = pure wheel, c79de2 = sdist and native wheels; py-rust-stemmers: 944905 = Windows wheel, f79ad9 = everything else).
- Cons: does nothing for NOTICE (still needs A or B) and the SBOM still hashes NOTICE bytes. A multi-digest list weakens the pin slightly (each digest needs a recorded review).

### Option D. Pin licence evidence to the sdist
For packages needing an exact digest, record the sdist licence file hash and verify against it instead of the installed wheel (fetch from the lock's sdist URL/hash, or compare against a vendored copy under `compliance/`).
- Files: `compliance/release-license-policy.json`, `scripts/release-license-policy.mjs`, optionally a vendored `compliance/licenses/<pkg>/...` directory.
- Pros: one digest per package and independent of wheel flavour; caio sdist = native wheels = Linux, py-rust-stemmers sdist = all non-Windows wheels [V].
- Cons: the sdist is not what is installed, so the check no longer verifies the shipped file (and the Windows pure-Python caio wheel's COPYING differs from the sdist's, so Windows would then be asserting a different file than the one it ships [V]; the two texts were not diffed, only hashed). It needs network or vendored data in CI. Does not help NOTICE.

### Option E. Drop `noticeSha256` coupling and/or make the checks CI-only
Remove the `noticeSha256`/`policySha256` embedding from the SBOM attestation, or run `check:notice`/`check:release-sbom` only in a dedicated release step instead of `test:next-ci`.
- Files: `scripts/generate-release-sbom.mjs` (lines ~175-177), `package.json` (`test:next-ci`), `bitbucket-pipelines.yml`, `scripts/required-ci-contract.test.mjs` (asserts lines 165-166 that `test:next-ci` contains both).
- Pros: stops unrelated NOTICE churn from invalidating the SBOM; unblocks PRs.
- Cons: weakens a release-compliance gate that was added on purpose (10-26); needs an owner decision; a hiding-the-red-check smell given the user's "do not work around" rule. I would not pick this alone.

### Suggested sequence (my recommendation, owner decision needed)
1. Fix the CI step first (install python/uv/`uv sync --frozen` in `nextjs-ci-node-contracts`, as the other steps do), otherwise nothing below is observable.
2. Choose the canonical platform for generation. Shortest path that actually goes green: A + C (Linux canonical; multi-digest or Linux digests; Windows-only packages as platform resolutions), then regenerate NOTICE and then the SBOM in that order, on a clean `npm ci` tree.
3. If the owner wants Windows and Linux to agree, do B afterwards; it is the only option that removes the underlying nondeterminism (installed-tree scanning).

## 4. Other cautions
- NOTICE depends on the local `node_modules` and `.venv` state (stale directories, dev group). Whatever option is chosen, generation must start from `npm ci` and `uv sync --frozen` in a clean tree [V by the stale Node packages].
- The SBOM SPDX ids embed an ordinal index, so unrelated dependency changes churn most ids [V].
- A leftover worktree copy exists at `.claude/worktrees/agent-aacf1ff7499aa67ba/` with an older contract test; ignore.
- Not checked: macOS/arm wheel digests; whether the real Bitbucket image has uv; actual Bitbucket build history.
