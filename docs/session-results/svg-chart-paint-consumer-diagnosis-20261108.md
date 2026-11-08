# Genuine column capture consumer diagnosis — 2026-11-08

The focused consumer run reported by root reached 160 passed and one failed after
the original seven-example static fixture was restored. No unchanged pytest run
was repeated for this diagnosis.

The immutable replay input manifest SHA-256 is
`8aeb95d518c9eac3c7ef2150b4723f3680e8f06a33999521ae1daa4985af8d3e`.
All 14 referenced artifact byte hashes were checked before one diagnostic-only
consumer call. The actual `SvgRenderResult` was restored from its four original
fields, including the unchanged decoded PNG and original quality. The call
returned `chart_content_paint_native_ink_unproven`.

The exact target is `native-chart-sales/glyph/svg-2/0/11`. Its cluster at UTF-16
range `[5, 6)` has a text digest equal to SHA-256 of U+0020 ASCII space, one
UTF-16 code unit, complete=true, pixelCount=0, alphaSum=0 and
observableReferencePixels=0. The producer demands native ink only when the
original cluster text has non-whitespace content. The consumer's finite blank
digest set accidentally omitted U+0020, so it treated this measured space as a
missing visible glyph.

Independent review rejected a classification-only fix: a nonblank owner could
otherwise claim a whitespace cluster digest. The corrected consumer keeps the
exact owned text already derived from the actual wrapper, binds every cluster
digest to its actual UTF-16 slice, then uses the producer's whitespace predicate.
The wire/context schema is unchanged. A valid surrogate-pair slice is accepted;
a range cutting that pair is rejected. UTF-16 coverage/ownership, complete flags,
selected-target font evidence and all nonblank ink/observable/original-contribution
requirements remain mandatory.

Regressions permit measured whitespace zero ink, reject nonblank zero ink at
the same ownership binding, reject a nonblank-to-space digest substitution after
coherent full-consumer outer rebinding, and cover a valid supplementary Unicode
character and a split surrogate range. The immutable genuine capture replay
remains unchanged.

Canonical SVG, pre-font wrapper and trusted admitted-render identities remain
separate. No capture, source, receipt, digest or artifact was restamped. No
browser, provider, database, server or export was run. No pytest or post-fix
consumer invocation has been performed by this task; independent review and
root's authorized focused rerun remain pending.
