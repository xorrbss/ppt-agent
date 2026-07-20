# Artifact to Authored style

`scripts/build-authored-style.py` analyzes one local PPTX or PDF and creates a
reviewable PPT-agent Authored style YAML draft. It is deterministic, offline, and
reuses the existing external-style converter and current authored-style loader.

The pipeline is deliberately separated:

1. Extract observable design signals into analysis JSON.
2. Normalize that evidence into the external converter input contract.
3. Convert it into Authored YAML.
4. Load the generated YAML through `load_authored_styles()` and compare the
   round-trip result before writing output.

The analysis records evidence, signal-level confidence, and warnings. Missing data
is marked unavailable. Exact colors used only to satisfy the operational preview
contract are labeled as unobserved fallbacks in the YAML brief.

## Usage

Run from the repository root with the FastAPI project's locked Python environment:

```bash
# Inspect the whole pipeline without writing files.
uv run --project servers/fastapi python scripts/build-authored-style.py \
  path/to/reference.pptx --output build/reference-draft.yaml \
  --analysis-output build/reference.analysis.json --dry-run

# Create both the loader-validated YAML and deterministic evidence JSON.
uv run --project servers/fastapi python scripts/build-authored-style.py \
  path/to/reference.pdf --output build/reference-draft.yaml \
  --analysis-output build/reference.analysis.json

# Choose an explicit converter-validated ID and replace only requested outputs.
uv run --project servers/fastapi python scripts/build-authored-style.py \
  path/to/reference.pdf --output build/reference-draft.yaml \
  --analysis-output build/reference.analysis.json \
  --id reference-draft --overwrite

# Show every option.
uv run --project servers/fastapi python scripts/build-authored-style.py --help
```

`--output` is required. Use a scratch or `build/` path: the command never selects
`servers/fastapi/authored_styles/` by default, so the official 30-style catalogue is
not modified accidentally. Existing YAML or analysis JSON is rejected unless
`--overwrite` is explicit. `--dry-run` still analyzes, normalizes, converts, and
round-trip validates, but creates neither parent directories nor files.

## Extracted signals

For PPTX, the analyzer reads OOXML theme RGB values and font schemes, explicit
fills/lines/text formatting, slide size, placeholder-based text roles, coarse
repeated geometry, and shape/image/chart/table/text composition. For PDF, it reads
supported grayscale/RGB/CMYK graphics colors, character font names and sizes, page
sizes, coarse repeated geometry, hyperlinks, and text/image/vector composition.

Composition is calculated with deterministic 100 by 100 occupancy grids per page or
slide. It is an approximate design signal, not a pixel-accurate segmentation.

## Safety and failure behavior

- No runtime network access, OCR, macro execution, external-link fetching, attachment
  extraction, JavaScript/action execution, or embedded-object opening is performed.
- PPTX is preflighted before `python-pptx` reads it: file size, archive member count,
  expanded size, per-member size/compression ratio, encrypted members, unsafe paths,
  and required OOXML parts are checked. Macro, embedded, and ActiveX/control parts
  make the builder fail closed before YAML or JSON is written. External relationship
  counts remain warnings and their targets are never followed.
- PDF is parsed with the locked `pdfplumber` dependency. Password-protected,
  unsupported-encryption, and extraction-prohibited documents fail clearly. A
  no-text PDF is labeled likely scanned only when image coverage supplies enough
  evidence; OCR is intentionally out of scope. Direct Flate, ASCII85, ASCIIHex, and
  RunLength filter pipelines receive a bounded preflight before pdfminer opens the
  file. Indirect filter/length declarations and filters that cannot be bounded by
  this preflight fail clearly. Serialized and safely decoded PDF name tokens for
  JavaScript, open/additional/launch actions, RichMedia, and embedded files are
  conservatively counted; any detection makes the builder fail closed before YAML
  or JSON is written, because a count can include an inactive token.
- Empty decks/documents, damaged files, unsupported suffixes, documents over 100 MiB,
  and decks/documents over 500 slides/pages fail before conversion.
- Absent fonts, colors, text hierarchy, composition objects, repeated layouts, and
  mixed PDF page sizes remain explicit warnings. Invalid page dimensions fail
  clearly. Theme values do not prove those colors/fonts were used.

Current declared parsers are `python-pptx` and `pdfplumber`. PyMuPDF and `pypdf` are
not required, and the workflow adds no dependency, database, API, or UI changes.

## Integration review

The generated file is a draft, not an automatic catalogue addition. Before copying
one into the official authored-style directory, inspect the source license, JSON
evidence, confidence levels, all warnings, font availability, palette heuristics,
and representative layouts. Then run the full FastAPI tests and verify that the
catalogue still contains the intended 30 IDs with no filename or ID collision.
