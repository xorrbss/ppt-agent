# Review and repair of the Codex SVG work (#147-#151) - 2026-11-08

Claude Code reviewed the Codex-authored merges #147-#151 (15 commits; about
15k lines of code plus about 30k lines of fixtures and evidence JSON), then
fixed the findings on branch `claude/exciting-planck-oqcy7w`. No paid provider
call, Office run or production setting change was made. V5, template pictures
and content sizing stay default-off.

## Review result (before the fixes)

| Area | Correctness | Design | Tests |
| --- | --- | --- | --- |
| Native chart V5, producer | 5 | 3 | 5 |
| Native chart V5, capture/consumer | 4 | 2 | 4 |
| #148 fix commits (11) | about 7 on average | | |
| Pictures / content sizing / evaluation | 7 / 6 / 5 | | |
| CI and repository hygiene | 2 | | |

The merges left the required gates red. Ruff had 173 errors where the commit
before #147 was clean. The structural-layering gate failed. The svg unit subset
had 48 new failures: it was 3 before Codex, 51 after.

## What was fixed (commits on the branch, oldest first)

1. `dec356b1f`: the CI gates.
   - `publish_svg_candidate` read `source` before assignment, an
     UnboundLocalError on every page-remake publish.
   - `utils -> services` import: the transform parser moved to
     `utils/svg_uniform_transform.py`.
   - Ruff repairs, checked to leave the AST unchanged.
   - The superseded converter alias, voided by the vendored `chart_data.py`
     patch, is removed (owner decision D5).
   - The PNG digest test now compares decoded pixels.
   - An admission contract key was missing from its test.
2. `e9035d263`: frontend.
   - A retried request now keeps its explicit verbosity.
   - Every SvgTemplatePicker draft request is guarded by a request epoch
     (stale responses, busy release).
   - The ref is no longer written during render.
   - Docs harnesses no longer hard-code `C:/...` paths.
3. `71af07de8`: v1 template decks refuse page moves to a position whose frame
   differs. The guard had become a no-op.
4. `f65d6ee0b`: V5 hardening.
   - Bar layout search capped at 4096; it previously ran for more than 120 s.
   - IndexError in `v5_proven`.
   - Doughnut hole outside 10-90% falls back to raster instead of failing the
     export.
   - Wider exception coverage.
   - Capture errors no longer cost an author repair.
   - CDP context-id inequality dropped; it made every capture incomplete on
     Chrome for Testing 153.
   - numpy masks, exact parity, about 12x faster.
   - Unreadable fonts and malformed SVG leave the proof incomplete.
5. `c83123b50`: evaluator.
   - `run_eval.py` silently recorded every run as `measurement_status=failed`
     when run as documented.
   - Content sizing is judged per card, and simple shapes get bounds.
   - A deck-level sizing scalar is added.
   - A completed `--retry-failed` attempt supersedes its failure in `decide`.
6. `b9d0d64d0`: picture-style robustness, a lock around the preset registry
   import, and the offer budget documented. CLAUDE.md now records that preset
   widening is kept ungated.
7. `1314c8531`: the `git show HEAD` self-comparison tests are replaced by a
   frozen pre-V5 fixture, `tests/unit/fixtures/svg_v5_legacy_parity_v1.json`.
8. `0f0e3e9b3`: one unprovable chart no longer withdraws the paint witness of
   the other charts on its page.
9. `f8fc52be0`: `<defs>`, title, desc and metadata no longer refuse V5 charts.
   A `class` attribute is refused only when the SVG has a stylesheet. `url()`
   paints are now refused explicitly; before, they were read as unpainted.
10. `43f1c3d5f`: faster lossless plane screenshots and numpy mask statistics.

Every change to a hash-pinned verifier source was re-pinned in
`services/verifier_revisions.py` under the current revision, with the reason.
V4 and legacy behaviour is unchanged; the frozen fixture and the legacy tests
check that.

## Verification

- `ruff check .` and the structural-layering gate pass.
- svg unit subset (495 files): 32 failures after the CI fix, against 51 at the
  Codex head.
  - 3 are pre-existing (`test_authored_illustration_plan_wiring`, failing
    before Codex).
  - 1 is a timing flake under load.
  - 28 were the Chromium V5 cases, later fixed by `f65d6ee0b` down to 2
    environment-dependent ones (next section).
- Full backend pytest, same environment and command (`pytest -n 6 --dist loadfile`):
  - 93 failures at the Codex head; 48 on this branch.
  - The 4 that appear only on this branch are environmental. A stale `./test.db`
    stamped at an old Alembic revision caused 3 `test_lifespan` failures, and
    `test_presentation_publication_durability` fails only when it shares that
    DB file with the lifespan tests. All 4 pass in isolation with a fresh DB.
  - Every other remaining failure is also present at the Codex head. Most are
    outside SVG: migrations, codex auth, workspace contracts, timeout and
    Hangul lint tests.
  - The CI svg subset is listed above.
- Fake-LLM harness (`scripts/fake_llm_e2e`): 5-slide svg generation plus PPTX
  export completed with V5 off and with V5 on (the deck pinned
  `svg-native-requirements/v5`). The fake authors no charts, so this proves
  plumbing only. `quality_pages.flagged=5` matches the Codex head exactly; it
  comes from the fake's plain pages.
- The Next.js node suite has 14 failures, identical at the Codex head and
  already present before Codex. The stale `NOTICE` (`check:notice`) and the
  `caio` SBOM license digest check also fail here and before Codex. The
  installed Linux wheel's licence file differs from the reviewed digest. None
  of these was introduced by this work.

## Still open, and why

- **V5 runtime budget (needs a decision).** A real-builder chart takes 58-110 s
  for 232+ fresh-document planes; the column case dropped from 83 s to 78 s.
  The product default is a 60 s worker and an 85 s client. The remaining cost
  comes from the proof's "fresh document per plane" design, at about 0.3 s per
  plane. Fitting the budget means batching planes, reusing documents, or
  running planes in parallel tabs. That changes the proof's soundness argument
  and needs an explicit owner decision. V5 must stay off until then.
- **Two environment-dependent Chromium tests.**
  - One reads `C:\Windows\Fonts\seguisym.ttf`.
  - The other sees 86 anti-aliased pixels for a same-colour glyph on Linux
    Chrome.
  Neither was skipped or loosened.
- **Witness manifest built from estimated text boxes.** The manifest is the
  renderer's input, so measured boxes do not exist yet when it is built. That
  review suggestion does not apply as stated.
- **Local paths in evidence JSON and Markdown.** Existing evidence files,
  older docs and a test fixture still carry `C:/Users/ibiz/...` paths. They
  were left as recorded provenance: one is digest-pinned, and older non-Codex
  documents carry the same.
- **Handoff items that need authorization.** The handoff's acceptance items
  (paid generation, Office rendering, the paired quality study) need the
  owner's authorization and were not started.

## Follow-up during the same session (multi-agent)

Further commits, each reviewed before integration:

- `4d2f08627`: SVG template UI literals moved to i18n catalogs. `check:i18n`
  passes.
- `0620cc818`: the Next.js timeout-signal tests awaited only an unref'd
  `AbortSignal.timeout`, so node:test cancelled them. They now keep the event
  loop alive; product code is unchanged.
- `4b92debf9`: 19 literal workspace error codes registered, and the errors
  contract regenerated.
- `66b15ae5d` and the registry snapshot commit: stale fixtures and test doubles
  repaired, each traced to the commit that made code and test disagree.
  - The only product bug in this batch: `alembic check` always reported drift
    for the ORM-less `workspace_generation_lineage_state`.
- Two decision memos (no code changes):
  - `notice-sbom-platform-analysis-20261108.md`: NOTICE and the SBOM are stale
    on every OS, wheel licence texts and digests differ by platform, and the CI
    step lacks uv.
  - `svg-v5-plane-budget-options-20261108.md`: in-target document reuse with
    full revalidation plus chart-only frame-pacing flags, prototyped at 30-43 s
    with byte-identical proofs.

### Final checks

- **Full backend pytest:** 21 failures, against 93 at the Codex head and 48
  earlier in this session.
- **Remaining backend failures, none introduced by this work:**
  - frozen authored/vNext paths that depend on Windows fonts, Linux clock
    behaviour or PostgreSQL (vNext e2e ×4, scheduler overload, checkpoint
    90-slide and process restart, capacity deferral, public export product
    path);
  - Windows-only paths in `test_trusted_executor_contracts`;
  - test DB pollution (`test_presentation_publication_durability` passes
    alone);
  - `test_authored_illustration_plan_wiring` (failing before Codex);
  - the two environment-dependent Chromium cases;
  - `test_public_v2_admission_errors`, left for an owner decision: one test's
    premise, that a non-raster style is rejected, was removed on purpose by
    the 2026-10 free-composition change.
- **Next.js node suite:** first batch 836 passed, 0 failed. The renderer batch,
  which never ran before because the first batch failed, has 2 Chrome-cleanup
  ECONNRESET failures in the frozen authored-hybrid path.
- **CI gates:** ruff, structural layering, the svg subset check and the
  required-CI contract test pass. `check:notice` and `check:release-sbom` fail
  as described in the analysis document.

## Owner-approved follow-up (same day)

- **V5 paint proof moved off the render path** (`137c5e611`, `2913e073e`, plus
  the preserved-source fix):
  - Render records `pending`. `export.pptx` runs the unchanged proof, seals
    digest-bound evidence in `slide.properties`, and exports a V5 chart
    natively only with current passed evidence; otherwise the chart stays
    drawn.
  - Preserved source charts that must export natively keep the in-render
    proof, so nothing is loosened.
  - Chart-proof Chrome launches use frame-pacing flags.
  - Known gaps: pages whose render inputs verification cannot reproduce, and
    decks that went through the header-design operation, keep V5 charts drawn.
    No real-Chrome end-to-end export was possible here.
- **NOTICE/SBOM interim step** (`456602d53`): the CI step installs uv; the
  policy allows reviewed per-platform licence digests; Linux is the canonical
  generation platform. NOTICE and the SBOM were regenerated, and
  `check:notice` and `check:release-sbom` pass. The end-state plan is in the
  analysis document, section 5.
- **V4/V5 disagreement measurement:** `svg-v4-v5-chart-disagreement-20261108.md`.
  On the 4 real stored charts, V5 has 1 true catch (dual axis) and 1 false
  rejection (unit caption).
- **Public v2 admission tests** realigned with the free-composition change.
- **Verification:**
  - svg subset: only the 2 environment-dependent Chromium cases fail. One
    earlier run showed a load flake in the header space probe that did not
    reproduce.
  - `test:next-ci`: everything passes up to the renderer batch, whose 2
    failures are the frozen authored-hybrid Chrome cleanup ECONNRESET.
  - Fake-LLM V5 generation and export completed.

## Real-chart V5 measurement and label rules (same day, before the Windows check)

- **Fake-LLM harness:** `FAKE_LLM_CHART_FIXTURES=1` authors the 7 real-builder
  chart examples. `run_export.sh` re-exports an existing deck.
- **Real Chrome, Linux, V5 on, final branch:**
  - every chart page passed render on the first attempt, including
    marker-line after the null-ink fix;
  - export 1 verified 3 charts in about 300 s, and export 2 brought the deck to
    6 native charts (bar, line, stacked, doughnut, fixed-axis bar, pie, each
    with its workbook);
  - each chart takes 66–117 s to verify;
  - one export verifies about 3 charts because of its deadline reserve. A
    follow-up verification run or a deadline that scales with the chart count
    is the open product decision.
- **Exact V5 label rules** (`2c8b2e848` integration):
  - half-up rounded labels and share labels, compared exactly;
  - digit-free prefixed captions outside the plot area treated as annotations
    and kept as drawn text on export;
  - short-name legend swatches treated as legend;
  - 39 positive and attack tests.
  - On the real smoke charts, the unit-caption chart now passes and the
    dual-axis chart fails for its real reason.
- **Null text-ink fragments** from the renderer no longer make the V5 scene
  reader reject a chart.
- **Windows/PowerPoint acceptance:** see
  `svg-v5-windows-acceptance-checklist-20261108.md`.
