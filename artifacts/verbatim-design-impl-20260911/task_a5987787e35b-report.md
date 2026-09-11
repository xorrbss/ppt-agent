# T5 placeholder authoring implementation

Task: `task_a5987787e35b`; dispatch: `ctx_4f766402e8aa`.
Offline implementation completed; no stack restart, provider call, database access, or live capture.

## Implementation

- Added `services/source_content/placeholder/`: bounded whole-block/character-range grammar, text-slot scanning on T3's pinned html5lib tree, server-owned block table, grapheme-safe exact-cover checks, one non-recursive insertion pass, deterministic ownership/stamping, bounded expansion/deviation receipts, T2 text-step selection, and the shared author/repair prompt contract.
- `expand_placeholders(html, block_table, *, context)` returns expanded HTML and bounded receipts. Substitution only writes etree text/tail data; T3's serializer performs HTML escaping. Source strings containing token syntax are never rescanned. Unknown, missing, duplicate, forbidden-context, and invalid-cover cases preserve the five requested `source_placeholder_*` codes.
- The scanner rejects tokens in attributes, comments, head/style/script/template/noscript text, explicitly hidden elements and their descendants. It calls the existing `_RepairVisibility.handle_starttag` predicate on parsed attributes; it does not introduce a CSS cascade or replace captured visibility checks.
- Range fragments aggregate under a common whole-block/projected-run owner. Inner emphasis wrappers remain unbound. Stamping calls the existing `text_stamping._stamp_node` and verifies `deterministic_source_qid`; no character-range binding IDs or new readback predicates were introduced. Within-slide/within-block DOM order deviations are bounded records.
- Added default-off registry entry `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` and `is_authored_verbatim_placeholder_authoring_enabled` accessor. The prompt addition in `author_slide_prompt.py` resolves the source unit from the immutable current AdmissionSession; the same `placeholder_prompt_contract` is available to T6. Existing page-marker/header furniture instructions remain.
- T2 owns its files. Its delivered hook uses `select_text_step`; the pre-normalization context hook is `check_placeholder_context`. Only token-state source-reflow/source-content-restyle inputs expand; expanded/captured inputs are not rescanned. T2's `TextStepResult.normalizations` carries the expansion receipt/deviations, and T2 retains post-expansion policy/limits and captured integrity checks.

## Coordinator-resolved contracts

The implementation follows the coordinator's explicit clarifications, which supersede the initial historical-byte assertion:

1. Offsets address the projection's **canonical accepted text**, not the raw normalized string. Optional projected run markers are furniture hints rather than inserted token text; required inter-run separators remain. `fullText` remains available for sizing/context. Real `source-slide:34:text:7` demonstrates why: its raw normalized string fails the unchanged `accepts` predicate because a discarded leading marker precedes the first projected run. The canonical rendering passes that same predicate. No `source_runs` or readback code changed.
2. Character ranges inside one projected run share one stamped owner; independent range QIDs would duplicate the existing run/block identity. No owner preference is inferred from pre-existing QIDs.
3. Published-fixture parity compares **base source occurrence IDs**, exact projected-run coverage, accepted text, deterministic unique QIDs, unchanged authored inline elements/attributes, and canonical serializer idempotence. Historical ancestor choice, run partition IDs, and serialized byte digests are not required to remain identical after canonicalization.
4. Historical `shared_owner`/`out_of_order` fixtures are explicitly classified as legacy adapter incompatibilities. They are not repaired or converted into manufactured parity passes. Design/spec-memory amendments remain coordinator-owned.

## Verification performed

Final focused command:

```text
.venv/Scripts/python.exe -m pytest tests/unit/test_source_placeholder.py tests/unit/test_source_placeholder_prompt.py tests/unit/test_source_placeholder_fixtures.py -q --tb=short
167 passed in 8.15s
```

- Grammar, malformed/nested tokens, token-looking source data, forbidden contexts, escaping, Korean/jamo/combining/ZWJ grapheme boundaries, duplicate/overlap/gap cover failures, furniture/occurrence distinction, stamping parity, order receipts, prompt switch, accessor default, and expanded/captured non-recursion are covered.
- Published decks: **105 slides classified; 90 exact/ordered fixtures passed the clarified parity assertions; 15 legacy incompatibilities** (run-16: 6, run-20: 5, run-21: 4). These are offline HTML text/projection assertions, not new Chromium readbacks. Full run/slide/block/deviation inventory: `fixtures/INCOMPATIBLE.json` beside this report.
- New token candidates built from the failing source units of runs 17/19/23 construct the expected source occurrences; omitting the named failing block returns `source_placeholder_missing`. Missing-author fixtures from runs 13/14/18/19 cannot become an admitted token document. Author exhaustion/flag-only decisions remain outside this module.
- The expanded fixture INDEX is selected by `has_author_html`, including run-17/09 despite `fixture_role=failing_slide`: **199 existing author HTML documents** are exercised as legacy expanded inputs and fail missing-placeholder checks if incorrectly presented as token inputs. They are not relabeled as successful token-mode generations.
- Ruff over all new modules/tests, the prompt/accessor, and the measurement script: **all checks passed**.
- `npm run test:settings-registry`: **passed**, 161 synchronized keys and 2 tests.
- `npm run test:file-size-ratchet`: **failed only on the ten documented pre-existing offenders**; none is a T5-owned file. No file-size limit or unrelated offender was changed.

Earlier focused failures exposed the canonical-marker and legacy-owner-contract conflicts above. The unsupported block-element owner rule was removed; the final command passed after the coordinator's explicit resolution. The concurrently refreshed 43-page fixture briefly disappeared during one earlier test run and was present in the final passing run.

## Prompt budget measurement

Actual 43-page manifest, pinned text payload fingerprint **`d3839b142c32`**:

| Measure | Result |
| --- | ---: |
| Source units | 43 |
| Text blocks | 965 |
| Projected runs | 1,317 |
| Entire encoded block table | 177,090 characters |
| Smallest per-source-unit table | 1,387 characters |
| Largest per-source-unit table | 6,855 characters |
| Existing structured-context limit | 64,000 characters |

The full-deck table exceeds the limit. The implemented prompt path supplies one source unit per slide; every unit fits, and `placeholder_prompt_contract` refuses an oversized table with a split-by-source-unit message. The 64,000-character limit was not raised. Reproduction script and detailed sizes: `task_a5987787e35b-measure.py` and `task_a5987787e35b-measurements.json`.

## Remaining live verification

The knob stays off. Coordinator-scheduled matched-input token-mode generation/publication, actual browser visibility/text/QID/capture evidence, repair-loop use of the shared contract, and export/Hybrid text-run behavior remain live rollout checks. Historical fixtures contain no captured browser metrics, so these unit results do not establish a newly published PPTX or visual acceptance.
