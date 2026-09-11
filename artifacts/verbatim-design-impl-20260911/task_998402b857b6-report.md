# T3 parser-based authored HTML normalization

## Result

Implementation commit: `c1ecc37f3` (`Add parser-based authored HTML normalization`).

The legacy source-span normalizer remains the default. The new tree path is
available through `normalize_authored_html_tree(html, *, profile)` and through
`normalize_authored_html(..., mode="tree")`; the registered
`AUTHORED_HTML_NORMALIZATION=legacy|tree` knob defaults to `legacy`.

## Parser and serialization contract

- Dependency: `html5lib==1.1`, pinned in `pyproject.toml` and `uv.lock` after
  coordinator approval. `webencodings==0.6.1` is its resolved runtime dependency.
- Tree builder: html5lib's `etree` builder, `strict=False`, with
  `namespaceHTMLElements=False`. Ordinary HTML element names are unqualified;
  SVG/MathML foreign namespaces remain intact.
- `document` profile: HTML5 document parsing and explicit canonical
  `<!DOCTYPE html><html><head>...</head><body>...</body></html>` serialization.
- `fragment` profile: HTML5 fragment parsing in a pinned HTML `div` context,
  without a wrapper in the serialized result.
- Serializer: optional tags are retained, attributes are alphabetized and
  always double-quoted, HTML sanitization is off, whitespace stripping is off,
  boolean attributes are minimized, and void trailing solidi are off. The
  serializer is idempotent on its canonical output.

## Raw diagnostics contract

`utils/authored_html_tree/raw_diagnostics.py` exposes
`diagnose_raw_authored_html(html: str) -> RawDiagnostics`. Its fields are
`byte_size`, `text_bytes_estimate`, `has_nul`,
`duplicate_attribute_findings: list[(tag, attr, count)]`, and
`violations: list[RawDiagnosticFinding(code, detail)]`.

The pass runs before HTML5 tree construction and mirrors only the existing
renderer input predicates that parsing can erase or obscure: source byte size,
NUL, node/depth/attribute-per-node/text-byte limits, attribute value size/NUL,
duplicate attributes, and the existing stray non-void closing-tag balance
predicate. Comments are not interpreted as markup. T2 can call this function
before either normalization mode; the tree entry point also refuses to parse
when a raw violation is present.

## Enumerated rewrite mapping

| Ordered rule | Legacy implementation | Tree implementation / receipt |
| --- | --- | --- |
| Browser-handled void end tags | `drop_void_element_end_tags` | html5lib tree construction; `html_void_end_tag_dropped` for ignored forms such as `</img>`, `html_void_end_tag_reparsed` where HTML5 reinterprets `</br>` as a new `br` |
| Break/page-break controls, orphans, widows | `authored_html_normalize._strip_forbidden_css` | `_rewrite_break_control_css`, sharing `strip_break_control_declarations`; `css_break_control_property_stripped` |
| Hidden element without bound text | `_normalize_hidden_elements` remove action | `_rewrite_hidden`; `hidden_element_without_bound_text_removed` |
| Hidden element with bound descendant/self | `_normalize_hidden_elements` strip action | `_rewrite_hidden`; `hidden_attribute_stripped_bound_text` |
| Title hard line breaks | `apply_title_line_break_rule` | `_rewrite_title_breaks`, reusing `is_title_role`; `title_role_line_break_replaced` |
| `name` attributes | `apply_name_attribute_rule` | `_rewrite_name_attributes`; `html_attribute_stripped` |
| Every non-allowed CSS declaration, including every `content:` declaration | `strip_disallowed_css` | `_rewrite_disallowed_css`, using the same CSS predicates through `strip_disallowed_declaration_list` / `strip_disallowed_stylesheet`; `css_property_stripped` with bounded property/value-class detail |
| CSS at-rules | `strip_disallowed_css` | same hoist/remove predicates through `strip_disallowed_stylesheet`; `css_at_rule_hoisted` / `css_at_rule_removed` |

Every tree receipt has exactly `rule`, `count`, and bounded `detail`; details
are capped at 120 characters and receipt entries at 64. Renderer tag,
attribute, CSS, SVG, image, font, and CSP policy content was not relaxed.

## Stored published-run parity and digest impact

I used the sanctioned `Read-StackEnvironment` in-process wrapper, a read-only
PostgreSQL transaction, and the immutable evidence-artifact reader. No stack
process was restarted or mutated. The comparison took the 35 final stored HTML
documents for each published run, applied the legacy and tree normalizers, and
submitted each result to the same `admit_authored_html` renderer policy.

| Run | Slides | Same verdict | Legacy admitted | Tree admitted | Tree digest changed |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16 | 35 | 35 | 35 | 35 | 35 |
| 20 | 35 | 35 | 35 | 35 | 35 |
| 21 | 35 | 35 | 35 | 35 | 35 |

The ordered-deck aggregate below is SHA-256 over the newline-joined sequence of
the 35 per-slide SHA-256 values. All 105 digests changed solely because the
tree serializer canonicalized representation, so capture reuse does not cross
the legacy/tree switch even when the policy verdict is identical.

| Run | Legacy aggregate digest | Tree aggregate digest |
| --- | --- | --- |
| 16 | `5d6d66d0069de36bb1cc8a60d5d6d1569a80f03b88fdd68bc171262e57448d05` | `ab67af44cf70de4fde1c8d880e4ab843e7da956a755492f1a774ac9e1295a072` |
| 20 | `1b205cfd9c8133c9b316d62ea0491760fc94ba2b46af460077bc1deb3e701209` | `d1732064a87abf6c9beffa70ce506d3b20c0ef91f069cce33f50b06785789803` |
| 21 | `daa90ac4dc64a71a730b353422a76a0c128fa5dde12cf04371f1f7e8a735c07a` | `6f74ec023c75a6e86d7a4c615e3df3f6a21f398b21d7a0a89717ee85042ca53a` |

## Historical failure-shape fixtures

- Run 13 `content:"1"` shape: raw digest
  `d6a835808a32aa652ae9dbbcb3b657bf440e420ecac1ce91814308e3ff97274f`,
  canonical digest
  `a3a6490ad8ea5012d75da2f2cb044bc44f791fb7f9ee38a35d2fd796205b55bd`.
  Outcome: admitted with
  `css_property_stripped(count=1, detail=content:text)`.
- Run 18 exact retained `</img>` fixture: raw digest
  `1eaae5c2fd5ededa12053fd5a152fee7f45b967c216c3af5f6624ebbfb99215e`,
  canonical digest
  `4f36377ce97934186689b584fd2c272e78b66f1d0a727613b5275e4a38dc6e36`.
  Outcome: admitted with
  `html_void_end_tag_dropped(count=1, detail=img)`.

## Verification

- `uv run pytest tests/unit/test_authored_html_tree.py tests/unit/test_authored_html_raw_diagnostics.py tests/unit/test_authored_html_normalize.py tests/unit/test_authored_css_strip.py -q`: **71 passed**.
- Follow-up raw-diagnostics check after adding the node/depth/attribute cases:
  `uv run pytest tests/unit/test_authored_html_raw_diagnostics.py -q`: **6 passed**.
- Ruff on all touched Python files: **clean**.
- `npm run test:settings-registry`: **passed**; 160 keys synchronized and both
  registry tests passed.
- Every new `utils/authored_html_tree` module is below 500 lines (largest: 201);
  `authored_html_normalize.py` is 444 lines.
- `npm run test:file-size-ratchet` was executed and failed only on concurrent,
  out-of-scope files. None is a T3-owned file. Reported offenders were
  `authored_checkpoint_state.py`, `authored_local_fit_evidence.py`,
  `authored_presentation_publication.py`,
  `presentation_export_direct_attempt_service.py`, `public_export_operation.py`,
  `workspace_artifact_publication_service.py`, `provider_idempotency.py`,
  `WorkspaceOperationCard.tsx`, `presentation-generation.ts`, `PresentonMode.tsx`,
  `authored-hybrid/export.ts`, and `authored-hybrid/slide-layer.ts`.
- A broader initial check including all renderer-isolation tests reached 126
  passes; after correcting one T3 test constant, one out-of-scope renderer-pipe
  fake remained failed with `Chrome DevTools pipe context is missing`. The
  renderer policy itself was also exercised successfully on all 105 stored
  documents and both historical fixtures above.

## Remaining live verification

The switch remains at its required `legacy` default. T2 still owns calling the
selected normalizer from the central gate. No provider generation, server
restart, or live Chromium capture was run; the coordinator should schedule the
fresh switched-mode series after the provider rate limit clears. That live
series must treat the changed canonical HTML digests as new capture identities.
