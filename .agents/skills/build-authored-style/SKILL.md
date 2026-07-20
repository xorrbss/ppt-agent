---
name: build-authored-style
description: Analyze one local PPTX or PDF into deterministic evidence JSON and a loader-validated PPT-agent Authored style YAML draft. Use when deriving a reusable Authored style from a presentation or PDF reference, inspecting which visual signals are extractable, or checking an artifact-derived draft before catalogue integration.
---

# Build Authored Style

Create an evidence-bounded style draft without executing macros, links, actions, or
embedded content. Treat warnings, unavailable signals, and operational fallbacks as
review items rather than source facts.

## Workflow

1. Work from the repository root. Confirm the input is one local `.pptx` or `.pdf`.
2. Choose output paths outside `servers/fastapi/authored_styles/`. Never write to the
   official catalogue unless the user explicitly requests a reviewed integration.
3. Run a dry-run first:

   ```bash
   uv run --project servers/fastapi python scripts/build-authored-style.py \
     path/to/reference.pptx --output build/reference-draft.yaml \
     --analysis-output build/reference.analysis.json --dry-run
   ```

4. If validation succeeds, rerun without `--dry-run`. Add `--id safe-style-id` only
   when the user needs a specific ID. Add `--overwrite` only after inspecting the
   existing requested outputs.
5. Read the analysis JSON before recommending the YAML. Report `evidence`,
   `confidence`, and every warning, especially missing text/font/color evidence,
   likely scanned PDFs, external links, and mixed page sizes. Active or embedded
   content fails closed before either output is written.
6. Confirm the authored loader accepts the generated file:

   ```bash
   uv run --project servers/fastapi python -c \
     "from pathlib import Path; from utils.authored_styles import load_authored_styles; print([s.id for s in load_authored_styles(Path('build'))])"
   ```

The CLI already follows the required pipeline: artifact analysis JSON, normalization
to the existing external converter input, Authored YAML conversion, then current
loader round-trip validation. Identical input bytes and options produce identical
analysis and YAML bytes.

## Boundaries

- Use only repository-locked `python-pptx` and `pdfplumber`; do not install parsers,
  use OCR, or access the network.
- One invocation accepts one file. It rejects empty, damaged, oversized, unsupported,
  password-protected, or extraction-prohibited inputs with a concise error.
- Supported direct PDF stream-filter pipelines receive a bounded preflight;
  declarations that cannot be bounded fail before parser open. Serialized and
  safely decoded action, JavaScript, RichMedia, and embedded-file markers are
  conservative detections and are never executed or opened. Any such detection,
  plus PPTX macro, ActiveX/control, or embedded parts, makes the builder fail
  closed without writing YAML or JSON. External hyperlinks remain warnings and
  are never followed.
- PPTX theme values and PDF graphics/text objects are partial evidence. Inherited
  styling, raster-only colors, visual meaning, and brand intent may be unavailable.
- The YAML is a reviewable draft. Operational preview fallbacks are labeled as
  unobserved and must never be described as extracted source values.
- Use `--help` for the complete CLI contract. `--dry-run` writes no YAML or JSON;
  existing output files require `--overwrite`.
