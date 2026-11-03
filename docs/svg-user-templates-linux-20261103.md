# SVG user templates on Linux and Docker (2026-11-03)

Follow-up to `svg-user-template-implementation-20261029.md`, which records the
feature as built and verified only with temporary material, never end to end.
This note records what blocked real use, what changed, and what a live run
showed. All runs: Linux VM, `claude-sonnet-5-5`, a three-slide sample PPTX
(navy header bar, orange divider, logo, footer "ACME Corp · Confidential").

## What blocked use

| # | Problem | Effect |
|---|---|---|
| 1 | `SVG_USER_TEMPLATES_ENABLED` was read but not registered | `test_internal_env_names` failed; no documented way to turn the gate on |
| 2 | The framed-template source preview needed PowerPoint over COM | every framed registration blocked off Windows (`svg_template_review_render_unavailable`) |
| 3 | Installed fonts were discovered only in Windows font folders | non-bundled, non-embedded fonts unavailable off Windows |
| 4 | Server-placed icon groups were named `asset-<slot>` | template bodies may carry only `body-` IDs: every icon page failed validation, deck failed |
| 5 | A template page without an intact render had no rescue | one bad page failed the whole template-redesign deck |
| 6 | The writing-policy scan read the template's fixed footer text | every page failed for the footer's middle dot, which the author cannot change |
| 7 | `svg_template_body_outside_allowed_region` carried no detail | the repair attempt could not act and repeated the overflow |
| 8 | The uploaded accent was the most saturated theme colour | Office's unused hyperlink blue `#0000FF` became the deck accent |
| 9 | Review-required states showed the generic "comparison failed" message | normal pre-approval steps read as errors |

## Changes

| # | Commit | Change |
|---|---|---|
| 1 | `a3bde1da8` | registered boolean, default false; compose and `docs/configuration.md` generated |
| 2, 3 | `421d6310c` | without Windows, LibreOffice converts the source to PDF (private profile; the template's own font bytes offered first through a private fontconfig) and pdfium rasterizes each frame page at the source canvas. Evidence: renderer `libreoffice`, `approximate: true`; publication accepts it for v1 evidence; the review screen says the preview is an approximation. Fonts discovered in `/usr/share/fonts`, `/usr/local/share/fonts` and the user font directories, bounded and symlink-confined, embedding policy unchanged |
| 5 | `ad12a05c3` | template fallback page: a deterministic body inside the frame's body region in the frame's own faces, composed and proven with the same furniture receipt and body-paint checks; outcome `svg_template_fallback_page`. v3 (fixed glyph preview) frames still fail |
| 4, 5 | `0080a878e` | icon groups on template-redesign pages are `body-asset-<slot>` (other decks unchanged, so stored responses replay byte for byte); a page without a valid render also reaches the template fallback |
| 6, 7 | `2db52e2d4` | template furniture text (`svg-system-template-*` except the body group) is not scanned; the overflow failure names the body region and each offending element with its measured box |
| 8 | `960f665e6` | the accent prefers colours the slides show (shape or text paint, rendered pixels); theme-only colours only when none; extraction version v3 |
| 9 | this note's commit | specific guidance for `source_render_review_required`, `classification_review_required`, LibreOffice codes |

## Live run

| Run | Result |
|---|---|
| framed registration → review → publish (API) | ready; fixed-region pixel difference 0.84/255 against the LibreOffice preview |
| same, through the upload screen (Playwright) | approximate notice shown, source and frame side by side, registered |
| template-redesign deck, 6 slides, before 4–7 | failed (5 pages `svg_template_body_namespace_required`) |
| same, after 4–5 | completed, 0/6 passed (3 template fallbacks, 2 flagged) |
| same, after 6–7 | **completed, 6/6 passed, PPTX exported**; two pages repaired an edge overflow (full-width rects whose 1px stroke crossed the region) on their second attempt |
| free-design deck from the framed template, 6 slides | completed, 6/6 passed, exported |
| style-only registration → free-design deck, 4 slides | completed, 4/4 passed, exported |

## Environment notes

* python-build-standalone (uv's managed CPython 3.11) lacks
  `POSIX_SPAWN_SETSID`; the isolated slide-capture renderer calls
  `os.posix_spawn(..., setsid=True)`, so template frame previews fail under it.
  The Docker images use `python:3.11-slim` (GCC build), which supports it.
  The runs above used the system `/usr/bin/python3.11` venv.
* python-pptx text boxes default to shape auto-fit, which the template
  compiler rejects (`svg_template_text_autofit_unsupported`, documented
  limit). Real templates with auto-fit text boxes need it turned off.

## Open

1. The default stays off (`SVG_USER_TEMPLATES_ENABLED=false`) until it is
   decided with the measurement in the deck-pictures note.
2. LibreOffice text layout differs from PowerPoint; exact comparison still
   needs a Windows server with PowerPoint.
3. Only one sample template was run. Real corporate templates (gradients,
   grouped shapes, auto-fit text) will hit the documented unsupported cases.
