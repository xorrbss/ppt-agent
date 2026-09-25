"""Pre-convert office inputs to PDF before LiteParse ever sees them.

``@llamaindex/liteparse``'s own converter spawns
``soffice --headless --invisible --convert-to pdf --outdir ...`` against the
*shared default* LibreOffice user profile for a fixed set of extensions (see
``convertOfficeDocument()`` in
``node_modules/@llamaindex/liteparse/dist/src/conversion/convertToPdf.js``,
its ``officeExtensions`` + ``spreadsheetExtensions`` lists). Two concurrent
LiteParse calls on office files can therefore collide on that shared
profile exactly like an unisolated bare ``soffice`` call would (see
``services/libreoffice_profile.py`` for the general problem).

``pdf_input_for_liteparse()`` pre-converts these extensions itself through
``DocumentConversionService``, which isolates the LibreOffice profile per
call, so files with an extension in ``LITEPARSE_SOFFICE_EXTENSIONS`` never
reach LiteParse's own converter and LiteParse never spawns soffice —
regardless of which caller hands ``LiteParseService`` a raw office file, now
or in the future.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from typing import Iterator

from services.document_conversion_service import DocumentConversionService

LOGGER = logging.getLogger(__name__)

LITEPARSE_SOFFICE_EXTENSIONS = frozenset(
    {
        ".doc",
        ".docx",
        ".docm",
        ".dot",
        ".dotm",
        ".dotx",
        ".odt",
        ".ott",
        ".ppt",
        ".pptx",
        ".pptm",
        ".pot",
        ".potm",
        ".potx",
        ".odp",
        ".otp",
        ".rtf",
        ".pages",
        ".key",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".ods",
        ".ots",
        ".csv",
        ".tsv",
        ".numbers",
    }
)


@contextlib.contextmanager
def pdf_input_for_liteparse(
    file_path: str,
    *,
    document_conversion_service: DocumentConversionService,
    timeout_seconds: int,
) -> Iterator[str]:
    """Yield a PDF path for ``file_path``, converting office inputs first.

    A no-op — yields ``file_path`` unchanged — for PDFs and for any
    extension LiteParse's own converter does not route through soffice
    (images go through ImageMagick or direct OCR instead; see
    ``liteparse_runner.mjs``'s ``DIRECT_OCR_IMAGE_EXTENSIONS``).

    Raises ``DocumentConversionError`` (unchanged) if the pre-conversion
    itself fails; callers decide how to surface that. The temporary
    conversion directory is removed on exit either way.
    """
    extension = os.path.splitext(file_path)[1].lower()
    if extension not in LITEPARSE_SOFFICE_EXTENSIONS:
        yield file_path
        return

    with tempfile.TemporaryDirectory(
        prefix="liteparse-office-convert-"
    ) as conversion_dir:
        LOGGER.info(
            "[LiteParse] Pre-converting to PDF via DocumentConversionService "
            "before invoking LiteParse file=%s",
            file_path,
        )
        converted_path = document_conversion_service.convert_office_to_pdf(
            file_path,
            conversion_dir,
            timeout_seconds=timeout_seconds,
        )
        yield converted_path
