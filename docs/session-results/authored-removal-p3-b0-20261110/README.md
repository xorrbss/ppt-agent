# authored removal P3, B0 baseline (2026-11-10)

Baseline for the P3 batches in `docs/authored-removal-execution-20261109.md`
("P3 plan"), taken on `main` at `f2b0a22ec`. No product code changed.

## Tool

`servers/fastapi/scripts/authored_removal_imports.py` (tests:
`servers/fastapi/tests/unit/test_authored_removal_imports.py`). Run from
`servers/fastapi` with `uv run python` (it needs the project venv to tell a
local namespace directory such as `alembic/` from the installed package that
shadows it).

- `closure --scope top|all --out FILE` follows package `__init__` modules,
  `sys.modules[__name__]` facades (including facade packages that alias their
  submodules, e.g. `services.authored_icon_pack`) and records every importer of
  each reached module.
- `check --delete LIST --baseline unresolved-baseline.json` is the per-batch
  done criterion. `LIST` holds repo- or root-relative paths or globs, one per
  line. It fails (exit 1) on any remaining non-test reference to the delete set
  in any scope (module level, `TYPE_CHECKING`, function-local,
  `import_module`/`__import__` strings, dotted string constants such as
  `patch()` targets) and on any `from X import name` that no longer resolves.
  Test references are listed separately (`test_hits`) for the batch to fix.

## Results

| File | Content |
|---|---|
| `svg-closure-top-authored.json` | Module-level closure from the three svg entry points: 626 modules, 175 authored/slidespec-named; every importer of each named module |
| `svg-closure-all-authored.json` | Same with function-local imports: 1,240 modules, 420 named |
| `unresolved-baseline.json` | Names that already fail to resolve on `main`: none |
| `check-empty-delete.json` | `check` with an empty delete set: 0 hits, 0 unresolved |

The 175/1,240 figures match the independent review in the P3 plan (it counted
1,239/419 for the `all` scope; the tool also follows `import_module` string
calls).

Sanity runs (not kept): deleting `services/authored_presentation_artifacts.py`
reports the five svg importers (including the function-local one in
`svg_raster_renumber.py:97`), the authored importers and five tests, three of
them string `patch()` targets; deleting `services/authored_vnext/generation`
reports `svg_generation_admission.py:162-163`, which `import server` cannot
catch.

## Test baseline: svg unit subset

The CI list (`servers/fastapi/tests/svg_unit_subset.txt`, 510 files with the
checker's own test) on this Windows checkout, with the CI environment
(`DISABLE_IMAGE_GENERATION=true`, SQLite, scratch `APP_DATA_DIRECTORY`),
`pytest -n 4 --dist loadfile`: **257 failed, 10,984 passed, 29 skipped,
6 xfailed in 19m51s**. Failing ids: `svg-subset-failures.txt`. Every later
batch compares against this list on the same machine.

The list file is CRLF in a Windows checkout; feed it as
`$(tr -d '\r' < tests/svg_unit_subset.txt)`, otherwise pytest collects nothing.

| Count | Cause | Kind |
|---:|---|---|
| 192 | `icon_family_digest_mismatch`: 11 icon-family files are LF in the index but CRLF in this checkout (`core.autocrlf`), so their digests differ; only `lucide/nodes.json` happens to be LF here | local checkout (no `-text` attribute for `assets/icon_families/`) |
| 18 | `svg_style_reference_size_limit` for `national-ai-msp-proposal`: an untracked style in this working tree | local working tree |
| 8 | `exact-osb-last` disabled or not allow-listed | local configuration |
| 3 | `unsupported_presentation_driver` in `test_authored_pipeline.py` (2) and `test_workspace_authored_template_confirmation.py` (1) | authored tests made stale by P2; P3 deletes them |
| 36 | other (header space advance, 16:9 baseline, vector illustrations, release evidence, font embedding, ...) | not classified; compare by id |

The full suite was not run here: about 50 minutes and 285 known failures on
Linux (`docs/test-failure-baseline-20261105.md`), and the local stack
(FastAPI 8000, PostgreSQL 21000) was running.
