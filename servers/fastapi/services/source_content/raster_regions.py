"""Conservative, local-only region suggestions for a raster slide.

Every text and rectangle returned here is a draft. OCR confidence is an engine
score, never evidence that the recognized text matches the source.
"""

from services.source_content.raster_region_types import OCRWord as OCRWord, RegionCandidate as RegionCandidate
from services.source_content.raster_region_panels import (
    _intersection as _intersection,
    _line_segments as _line_segments,
    _background_panel_candidates as _background_panel_candidates,
    _dark_toolbar_candidates as _dark_toolbar_candidates,
    _border_card_bottom as _border_card_bottom,
)
from services.source_content.raster_region_visuals import (
    _horizontal_edges as _horizontal_edges,
    _vertical_support as _vertical_support,
    screenshot_rectangles as screenshot_rectangles,
    _right_visual_envelope as _right_visual_envelope,
    _colored_icon_candidates as _colored_icon_candidates,
)
from services.source_content.raster_region_ocr import (
    full_page_ocr as full_page_ocr,
    ocr_density_rectangles as ocr_density_rectangles,
    text_and_table_candidates as text_and_table_candidates,
)
