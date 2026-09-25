"""Number guard for plans built without the vision structurer.

The heuristic plan has no second reader, so every OCR word carrying digits is
re-read on its own (English model at a steady glyph height, and a digit-only
read for words without Hangul; see ``raster_text_guards``). A text region is
published only if each of its number tokens is confirmed by that targeted read
(the read equals it, or extends it by a digit the English model invented for a
Hangul glyph). Otherwise the whole region, as one paragraph, stays pixels.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from services.source_content.raster_redesign_ocr import RedesignLine
from services.source_content.raster_text_guards import NUMBER, _compact, _number_ok, targeted_number_reads


def guard_numbers(plan, words, image, executable, tessdata):
    digit_words = sorted(
        {
            k
            for region in plan.regions
            if region.role in ("title", "body") and not region.editable_fallback
            for k in region.word_indices
            if any(ch.isdigit() for ch in words[k].text)
        }
    )
    if not digit_words:
        return plan
    pseudo = tuple(RedesignLine((k,), words[k].bbox, float(words[k].bbox[3])) for k in digit_words)
    reads = targeted_number_reads(image, words, pseudo, executable=executable, tessdata=tessdata)
    confirmed = {
        k
        for n, k in enumerate(digit_words)
        if all(_number_ok(number, reads.get(n, ()), Counter()) for number in NUMBER.findall(_compact(words[k].text)))
    }
    regions = []
    for region in plan.regions:
        risky = region.role in ("title", "body") and not region.editable_fallback
        if risky and any(k in digit_words and k not in confirmed for k in region.word_indices):
            region = replace(
                region,
                role="screenshot",
                editable_fallback=True,
                reason="paragraph_pixels",
                review_reason="unconfirmed_number",
            )
        regions.append(region)
    return replace(plan, regions=tuple(regions))
