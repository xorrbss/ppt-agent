# PDF Parsing Quality Fixes — Design Spec

## Problem

When users upload scanned PDFs (e.g., Turkish textbooks from eba.gov.tr), the extracted text has gaps, missing characters, and no images. Root causes:

1. **OCR language hardcoded to English** — the `/decompose` endpoint doesn't pass `presentation_language` to `DocumentsLoader`, so Tesseract always uses the English model
2. **DPI too low for scanned docs** — default 120 DPI; scanned PDFs need 300+ for reliable OCR
3. **Timeout too short** — `LiteParseService` defaults to 180s; large scanned PDFs at higher DPI need more time
4. **Single worker** — `num_workers=1` is slow; the JS runner already defaults to `CPU cores - 2`

## Scope

- **In scope:** Language passing (backend + frontend), adaptive DPI, timeout increase, worker count alignment
- **Out of scope:** Image extraction in decompose endpoint (unused by any consumer; separate `/pdf-slides` endpoint exists)

## Changes

### 1. Language Passing — Backend

**File:** `servers/fastapi/api/v1/ppt/endpoints/files.py`

Add optional `presentation_language` body parameter to the `/decompose` endpoint and forward it to `DocumentsLoader`:

```python
@FILES_ROUTER.post("/decompose", response_model=List[DecomposedFileInfo])
async def decompose_files(
    file_paths: Annotated[List[str], Body(embed=True)],
    presentation_language: Annotated[Optional[str], Body()] = None,
):
    ...
    documents_loader = DocumentsLoader(
        file_paths=other_files,
        presentation_language=presentation_language,
    )
```

No changes to `DocumentsLoader` — it already accepts and uses `presentation_language`.

### 2. Language Passing — Frontend

**File:** `servers/nextjs/app/(presentation-generator)/services/api/presentation-generation.ts`

Update `decomposeDocuments()` to accept and send `presentationLanguage`:

```typescript
static async decomposeDocuments(filePaths: string[], presentationLanguage?: string) {
    const body: Record<string, unknown> = { file_paths: filePaths };
    if (presentationLanguage) {
        body.presentation_language = presentationLanguage;
    }
    // ... existing fetch logic
}
```

Update call sites to pass the user's selected language.

### 3. Adaptive DPI

**File:** `servers/fastapi/services/documents_loader.py`

Add `_is_scanned_pdf()` method:
- Uses pdfplumber to sample first 5 pages
- If total extracted text < 50 chars across sampled pages, classify as scanned
- Returns boolean

Update `load_pdf()`:
- Call `_is_scanned_pdf()` before parsing
- Pass `dpi=300` for scanned, `dpi=120` for text-based

**File:** `servers/fastapi/services/liteparse_service.py`

Add `dpi` parameter to `parse()` and `parse_to_markdown()` so callers can override the default:

```python
def parse(self, file_path, ocr_enabled=True, ocr_language="eng", dpi=None):
    effective_dpi = dpi if dpi is not None else self.dpi
    # use effective_dpi in command
```

### 4. Timeout

**File:** `servers/fastapi/services/liteparse_service.py`

Change constructor default:
```python
def __init__(self, timeout_seconds: int = 600):  # was 180
```

### 5. Workers

**File:** `servers/fastapi/services/liteparse_service.py`

Change default:
```python
_DEFAULT_NUM_WORKERS = max(os.cpu_count() - 2, 1)  # was 1
```

## Files Modified

| File | Changes |
|------|---------|
| `servers/fastapi/api/v1/ppt/endpoints/files.py` | Add `presentation_language` param to `/decompose` |
| `servers/fastapi/services/documents_loader.py` | Add `_is_scanned_pdf()`, update `load_pdf()` with adaptive DPI |
| `servers/fastapi/services/liteparse_service.py` | Add `dpi` param, update timeout default, update workers default |
| `servers/nextjs/.../presentation-generation.ts` | Pass language in `decomposeDocuments()` |

## Testing

- Upload a Turkish scanned PDF → verify OCR uses Turkish Tesseract model
- Upload a text-based PDF → verify it still parses at 120 DPI (fast path)
- Upload a large scanned PDF → verify it doesn't timeout (600s + 300 DPI + multi-worker)
- Run existing tests: `pytest` in `servers/fastapi/`
- Run frontend build: `npm run build` in `servers/nextjs/`
