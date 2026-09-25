"""Minimal perceptual-hash helpers (no imagehash dependency)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

HASH_SIZE = 16  # 256-bit average hash


def ahash_bits(image_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert("L").resize((HASH_SIZE, HASH_SIZE), Image.LANCZOS)
        arr = np.asarray(im, dtype=np.float64)
    avg = arr.mean()
    return (arr > avg).flatten()


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


def image_pixel_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as im:
        return im.size


def best_bottom_crop_match(candidate_bits: np.ndarray, source_image_path, *,
                            max_top_fraction: float = 0.35, steps: int = 20) -> tuple[int, float]:
    """Best hamming distance between `candidate_bits` and a bottom crop of the
    source page image (top_fraction of the page height removed), scanning
    top_fraction from 0 to max_top_fraction. Returns (best_distance, best_top_fraction).

    Used to recognize a "page-content" crop -- the source content area below
    the title kept as one picture -- which a whole-page aHash comparison
    would not match closely (different pixel content), but a bottom-crop
    comparison will.
    """
    with Image.open(source_image_path) as im:
        gray = im.convert("L")
        sw, sh = gray.size
        best_dist = None
        best_frac = 0.0
        for i in range(steps + 1):
            top_fraction = max_top_fraction * i / steps
            y0 = int(sh * top_fraction)
            if y0 >= sh - 1:
                continue
            crop = gray.crop((0, y0, sw, sh)).resize((HASH_SIZE, HASH_SIZE), Image.LANCZOS)
            arr = np.asarray(crop, dtype=np.float64)
            bits = (arr > arr.mean()).flatten()
            dist = int(np.count_nonzero(bits != candidate_bits))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_frac = top_fraction
        return best_dist if best_dist is not None else 999, best_frac
