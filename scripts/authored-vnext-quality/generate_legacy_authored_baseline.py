"""Generate the frozen Authored image-PPTX baseline for visual comparison.

This invokes the existing low-level Authored author/illustrate/render adapter
directly.  It intentionally does not reopen a public legacy creation route.
The manifest is the same one consumed by ``visual_acceptance_census.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.authored_presentation_service import _author_illustrate_render_pipeline
from services.runtime_settings_coordinator import (
    start_runtime_settings_coordinator,
    stop_runtime_settings_coordinator,
)
from utils.authored_styles import resolve_authored_style
from utils.llm_calls.author_deck import build_image_pptx
from utils.llm_calls.author_slide import Brand
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def _outline(manifest: dict) -> PresentationOutlineModel:
    slides = manifest.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("representative_manifest_slides_required")
    values: list[SlideOutlineModel] = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise ValueError("representative_manifest_slide_invalid")
        required = slide.get("requiredText")
        if not isinstance(required, list) or not all(
            isinstance(item, str) and item.strip() for item in required
        ):
            raise ValueError("representative_manifest_required_text_invalid")
        title = slide.get("title")
        summary = slide.get("summary")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("representative_manifest_title_invalid")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("representative_manifest_summary_invalid")
        values.append(
            SlideOutlineModel(
                content=(
                    f"# {title}\n"
                    f"{summary}"
                )
            )
        )
    return PresentationOutlineModel(slides=values)


def _owner_id(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise ValueError("owner_id_uuid_required")
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        # Do not include the supplied identifier in CLI output or tracebacks.
        raise ValueError("owner_id_uuid_required") from None


async def _run(args: argparse.Namespace) -> dict[str, object]:
    owner_id = _owner_id(args.owner_id)
    manifest_path = Path(args.content_manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outline = _outline(manifest)
    style = resolve_authored_style(args.authored_style)
    owner_token = set_current_owner_id(owner_id)
    try:
        await start_runtime_settings_coordinator()
        try:
            result = await _author_illustrate_render_pipeline(
                outline,
                Brand(
                    topic="Authored 시각 품질 동등성",
                    language="Korean, Chinese, and Japanese exactly as supplied",
                    primary="#1473E6",
                    fonts="Noto Sans KR",
                    wordmark="Authored baseline",
                    primary_is_explicit=True,
                    fonts_are_explicit=True,
                ),
                style,
                instructions=(
                    "같은 콘텐츠의 기존 Authored 시각 기준선입니다. 한글/CJK와 카드, "
                    "프로세스, 타임라인, 비교, 메트릭, 인포그래픽, 표·차트 내용을 "
                    "잘림·겹침 없이 표현하세요. 각 슬라이드의 제목과 본문 문자열은 "
                    "번역·요약·의역하지 말고 입력 그대로 모두 보존하세요."
                ),
            )
        finally:
            await stop_runtime_settings_coordinator()
    finally:
        reset_current_owner_id(owner_token)
    if len(result.pngs) != len(outline.slides):
        raise RuntimeError("legacy_authored_render_count_mismatch")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    build_image_pptx(result.pngs, str(output))
    html_dir = Path(args.html_dir).resolve()
    html_dir.mkdir(parents=True, exist_ok=True)
    for index, html in enumerate(result.deck.htmls, start=1):
        (html_dir / f"slide-{index}.html").write_text(html, encoding="utf-8")
    return {
        "schemaVersion": "legacy-authored-visual-baseline/v1",
        "contentId": manifest.get("contentId"),
        "slideCount": len(result.pngs),
        "pptx": str(output),
        "pptxSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "designSystem": result.deck.design_system,
        "elapsedSeconds": result.elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--html-dir", required=True)
    parser.add_argument("--authored-style", default="default")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = asyncio.run(_run(args))
    receipt_path = Path(args.receipt).resolve()
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
