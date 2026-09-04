"""Style-conformance pre-filter for the Composer blind rating pack.

Reuses the actual feature-extraction functions from
``output/hyundai-reference-90plus-20260901/evaluate_visual_system.py``
(loaded by file path, not copied) but drives them over a 43-page candidate
against that script's 10-page reference instead of its fixed 10-vs-10
assertion, because the candidate here is the 43-page deck-43 Composer
output, not a 10-slide deck.

Per design bar doc section 7b this pre-filter "catch[es] palette and density
drift between rating rounds" and is explicitly "never the gate itself" --
every result returned here carries ``"gate": False`` and must be reported as
advisory only, never as a phase-1 acceptance condition.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

# Mirrors the fixed weights/tolerances defined inside main() of
# output/hyundai-reference-90plus-20260901/evaluate_visual_system.py
# (2026-09-01); that file's own feature/deck_stats/closeness functions are
# loaded and called directly below, not reimplemented.
_FIXED_TOLERANCE = {
    "light_base": 0.18,
    "ink_density": 0.18,
    "green_accent": 0.12,
    "deep_green": 0.12,
    "navy_panel": 0.10,
    "warm_accent": 0.08,
    "saturation": 0.12,
    "edge_density": 0.10,
    "edge_center_x": 0.18,
    "edge_center_y": 0.18,
    "top_header_ink": 0.20,
    "top_green_rule": 0.12,
    "bottom_band_dark": 0.16,
}
_WEIGHTS = {
    "light_base": 1.2,
    "ink_density": 1.0,
    "green_accent": 1.6,
    "deep_green": 1.3,
    "navy_panel": 1.1,
    "warm_accent": 0.3,
    "saturation": 0.8,
    "edge_density": 1.2,
    "edge_center_x": 0.6,
    "edge_center_y": 0.6,
    "top_header_ink": 0.8,
    "top_green_rule": 1.0,
    "bottom_band_dark": 0.8,
}
_DIVERSITY_KEYS = ("green_accent", "deep_green", "navy_panel", "edge_density", "bottom_band_dark")


def _load_reference_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("_composer_blind_rating_visual_system_ref", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load style pre-filter script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_style_prefilter(candidate_dir: Path, reference_dir: Path, script_path: Path) -> dict:
    """Advisory palette/density conformance of ``candidate_dir`` against ``reference_dir``."""

    module = _load_reference_module(script_path)
    references = sorted(reference_dir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    candidates = sorted(candidate_dir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if not references or not candidates:
        raise RuntimeError("missing reference or candidate PNGs for the style pre-filter")

    ref_mean, ref_std = module.deck_stats(references)
    out_mean, out_std = module.deck_stats(candidates)

    component_scores = {}
    for key_name in ref_mean:
        tolerance = max(_FIXED_TOLERANCE[key_name], ref_std[key_name] * 2.0)
        component_scores[key_name] = module.closeness(out_mean[key_name], ref_mean[key_name], tolerance) * 100.0
    mean_score = sum(component_scores[k] * _WEIGHTS[k] for k in _WEIGHTS) / sum(_WEIGHTS.values())
    diversity = float(
        np.mean(
            [
                module.closeness(out_std[k], ref_std[k], max(0.08, ref_std[k] * 2.0))
                for k in _DIVERSITY_KEYS
            ]
        )
    ) * 100.0
    geometry = (
        100.0
        if all(abs(Image.open(p).size[0] / Image.open(p).size[1] - 16 / 9) < 1e-6 for p in candidates)
        else 0.0
    )
    final_score = round(mean_score * 0.82 + diversity * 0.13 + geometry * 0.05, 2)
    return {
        "advisory": True,
        "gate": False,
        "note": (
            "Pre-filter only, per design bar doc section 7b: catches palette and "
            "density drift between rating rounds; it is NOT a phase-1 acceptance "
            "condition and must not be treated as one."
        ),
        "method": (
            "hyundai-proposal-visual-system-similarity/v1 feature extraction, reused "
            "from evaluate_visual_system.py; aggregation adapted here from that "
            "script's fixed 10-vs-10 comparison to 43-vs-10"
        ),
        "sourceScript": str(script_path),
        "referenceSlideCount": len(references),
        "candidateSlideCount": len(candidates),
        "score": final_score,
        "meanStyleScore": round(mean_score, 2),
        "deckDiversityScore": round(diversity, 2),
        "geometryScore": geometry,
        "components": {key_name: round(value, 2) for key_name, value in component_scores.items()},
    }
