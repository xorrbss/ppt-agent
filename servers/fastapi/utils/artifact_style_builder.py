"""Normalize artifact analysis into the external converter and authored loader path."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from utils.artifact_style_analysis import (
    ArtifactAnalysisError,
    analysis_json_bytes,
    analyze_artifact,
)
from utils.authored_style_conversion_io import ConversionResult, convert_data
from utils.authored_style_converter import ConversionError


@dataclass(frozen=True)
class ArtifactStyleBuildResult:
    analysis: dict[str, Any]
    analysis_target: Path | None
    conversion: ConversionResult


def _observed_values(
    analysis: Mapping[str, Any], signal: str
) -> list[Mapping[str, Any]]:
    signals = analysis.get("signals")
    if not isinstance(signals, Mapping):
        return []
    value = signals.get(signal)
    if not isinstance(value, Mapping) or value.get("status") != "observed":
        return []
    values = value.get("values")
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, Mapping)]


def _rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _luminance(value: str) -> float:
    red, green, blue = _rgb(value)
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def _saturation(value: str) -> float:
    red, green, blue = (channel / 255 for channel in _rgb(value))
    return colorsys.rgb_to_hsv(red, green, blue)[1]


def _palette(analysis: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    values = [
        str(item.get("value"))
        for item in _observed_values(analysis, "colors")
        if isinstance(item.get("value"), str)
    ]
    values = list(dict.fromkeys(value for value in values if len(value) == 7))
    if not values:
        return (
            {
                "background": "Operational fallback #FFFFFF; not observed in the source.",
                "primary": "Operational fallback #2563EB; not observed in the source.",
                "text": "Operational fallback #0F172A; not observed in the source.",
                "border": "Operational fallback #E2E8F0; not observed in the source.",
            },
            True,
        )

    background = max(values, key=lambda value: (_luminance(value), value))
    accent_candidates = [value for value in values if value != background]
    accent = max(
        accent_candidates or [background],
        key=lambda value: (
            _saturation(value),
            abs(_luminance(value) - _luminance(background)),
            value,
        ),
    )
    text = min(values, key=lambda value: (_luminance(value), value))
    border = min(
        values,
        key=lambda value: (abs(_luminance(value) - 0.75), value),
    )
    return (
        {
            "background": f"Observed color {background}; lightest sampled color heuristic.",
            "primary": f"Observed color {accent}; saturation/contrast heuristic.",
            "text": f"Observed color {text}; darkest sampled color heuristic.",
            "border": f"Observed color {border}; mid-light sampled color heuristic.",
        },
        False,
    )


def _typography(analysis: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    fonts = [
        str(item.get("family"))
        for item in _observed_values(analysis, "fonts")
        if isinstance(item.get("family"), str)
    ]
    hierarchy = _observed_values(analysis, "text_hierarchy")
    sizes = [
        float(item["size_pt"])
        for item in hierarchy
        if isinstance(item.get("size_pt"), (int, float))
    ]
    if not fonts:
        family_text = "Unobserved; do not claim or force a source font family."
    else:
        family_text = ", ".join(fonts[:4]) + " (observed; verify font availability)."
    if not sizes:
        hierarchy_text = (
            "Unobserved; retain a clear hierarchy without claiming source sizes."
        )
    else:
        unique_sizes = list(dict.fromkeys(sizes))
        hierarchy_text = "Observed sizes: " + ", ".join(
            f"{size:g} pt" for size in unique_sizes[:6]
        )
    return (
        {
            "font_families": family_text,
            "text_hierarchy": hierarchy_text,
            "usage": "Use only observed evidence above; treat this as a draft requiring human review.",
        },
        not fonts or not sizes,
    )


def _layout_templates(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    layouts = _observed_values(analysis, "repeated_layouts")
    templates: list[dict[str, Any]] = []
    for index, layout in enumerate(layouts[:5], start=1):
        count = int(layout.get("count", 0))
        signature = str(layout.get("signature", "unknown"))
        templates.append(
            {
                "type": f"source-layout-{index}",
                "usage": f"Coarse geometry signature {signature} repeated {count} times; reproduce only after visual review.",
                "components": [
                    "source-observed geometry",
                    "content-dependent hierarchy",
                ],
            }
        )
    if not templates:
        templates.append(
            {
                "type": "source-page-pattern",
                "usage": "No repeated source layout was observed; use a restrained layout consistent with available evidence.",
                "components": ["evidence-bounded draft", "human review required"],
            }
        )
    return templates


def analysis_to_converter_input(
    analysis: Mapping[str, Any], *, style_id: str | None = None
) -> dict[str, Any]:
    """Convert analysis JSON into the existing external-style converter schema."""
    source = analysis.get("source")
    document = analysis.get("document")
    if not isinstance(source, Mapping) or not isinstance(document, Mapping):
        raise ConversionError("analysis must contain source and document mappings")
    filename = str(source.get("filename", "artifact"))
    artifact_format = str(source.get("format", "artifact")).upper()
    page_count = int(document.get("page_count", 0))
    page_size = document.get("page_size")
    page_size_text = "Page size was unavailable."
    if isinstance(page_size, Mapping):
        page_size_text = (
            f"Observed canvas {page_size.get('width')} x {page_size.get('height')} "
            f"{page_size.get('unit')} (aspect {page_size.get('aspect_ratio')})."
        )

    palette, palette_fallback = _palette(analysis)
    typography, type_fallback = _typography(analysis)
    composition = analysis.get("signals", {}).get("composition", {})  # type: ignore[union-attr]
    shares = (
        composition.get("element_area_share", {})
        if isinstance(composition, Mapping)
        else {}
    )
    composition_text = "No element-area evidence was extractable."
    if isinstance(shares, Mapping) and shares:
        composition_text = "Observed bounding-box area shares: " + ", ".join(
            f"{key} {float(value):.1%}" for key, value in sorted(shares.items())
        )
    warnings = analysis.get("warnings")
    warning_codes = (
        [
            str(item.get("code"))
            for item in warnings
            if isinstance(warnings, list) and isinstance(item, Mapping)
        ]
        if isinstance(warnings, list)
        else []
    )
    warning_text = ", ".join(warning_codes) if warning_codes else "none"
    confidence_summary = ", ".join(
        f"{name}={value.get('confidence', 'none')}"
        for name, value in sorted(analysis.get("signals", {}).items())  # type: ignore[union-attr]
        if isinstance(value, Mapping)
    )
    fallback_text = (
        " Operational preview fallbacks are present and are not source evidence."
        if palette_fallback or type_fallback
        else ""
    )
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip() or "Artifact"
    external: dict[str, Any] = {
        "name_ko": f"{stem} 아티팩트 스타일",
        "description_ko": (
            f"{filename}에서 오프라인으로 관찰 가능한 신호만 정리한 Authored 스타일 초안입니다. "
            "분석 JSON의 증거·경고·신뢰도를 검토한 뒤 사용하세요."
        ),
        "description": (
            f"Evidence-bounded draft from {artifact_format} {filename}; {page_count} page(s). "
            f"Confidence: {confidence_summary or 'none'}. Warnings: {warning_text}.{fallback_text}"
        ),
        "category": "general",
        "tags": ["artifact-draft", artifact_format.lower(), "human-review"],
        "use_cases": ["source-informed presentation draft", "design-system review"],
        "design_system": {
            "global_style": {
                "theme": (
                    "Use only source-observed signals. Do not infer missing brand facts. "
                    f"{page_size_text} Review warnings: {warning_text}."
                ),
                "typography": typography,
                "color_palette": palette,
                "key_visual_elements": [
                    page_size_text,
                    composition_text,
                    f"Evidence confidence: {confidence_summary or 'none'}.",
                ],
            },
            "image_generation_prompts": {
                "style_guidelines": (
                    "Match only observed palette and composition signals; do not copy logos, "
                    "invent brand assets, follow external links, or treat fallbacks as evidence."
                ),
                "themes": [
                    {
                        "target": "source-consistent imagery",
                        "prompt_elements": "Use the analysis JSON as evidence and keep unobserved traits unspecified.",
                    }
                ],
            },
        },
        "slide_layout_templates": _layout_templates(analysis),
    }
    if style_id is not None:
        external["id"] = style_id
    return external


def _preflight_analysis_target(
    target: Path | None,
    source: Path,
    yaml_target: Path,
    overwrite: bool,
) -> Path | None:
    if target is None:
        return None
    target = target.expanduser().resolve(strict=False)
    if target.suffix != ".json":
        raise ConversionError(f"{target}: analysis output must use .json")
    yaml_target = yaml_target.resolve(strict=False)
    if target == source.resolve():
        raise ConversionError(f"{target}: analysis output must be a separate file")
    if (
        target == yaml_target
        or target in yaml_target.parents
        or yaml_target in target.parents
    ):
        raise ConversionError(
            f"{target}: analysis and YAML output paths must not overlap"
        )
    if target.exists():
        if not target.is_file() or target.is_symlink():
            raise ConversionError(f"{target}: analysis output is not a regular file")
        if not overwrite:
            raise ConversionError(f"{target}: output exists (use --overwrite)")
    if target.parent.exists() and not target.parent.is_dir():
        raise ConversionError(f"{target.parent}: output parent is not a directory")
    return target


def _write_atomic(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def build_authored_style(
    input_path: Path | str,
    output_path: Path | str,
    *,
    analysis_output: Path | str | None = None,
    style_id: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ArtifactStyleBuildResult:
    """Analyze, normalize, convert, round-trip validate, and optionally write outputs."""
    source = Path(input_path).expanduser().resolve()
    yaml_target = Path(output_path).expanduser().resolve(strict=False)
    analysis = analyze_artifact(source)
    external = analysis_to_converter_input(analysis, style_id=style_id)
    conversions = convert_data(
        external,
        source,
        yaml_target,
        overwrite=overwrite,
        dry_run=True,
    )
    if len(conversions) != 1:
        raise ArtifactAnalysisError(
            "artifact conversion produced an unexpected result count"
        )
    analysis_target = _preflight_analysis_target(
        Path(analysis_output) if analysis_output is not None else None,
        source,
        conversions[0].target,
        overwrite,
    )
    if not dry_run:
        conversions = convert_data(
            external,
            source,
            yaml_target,
            overwrite=overwrite,
            dry_run=False,
        )
    if not dry_run and analysis_target is not None:
        _write_atomic(analysis_target, analysis_json_bytes(analysis))
    return ArtifactStyleBuildResult(analysis, analysis_target, conversions[0])
