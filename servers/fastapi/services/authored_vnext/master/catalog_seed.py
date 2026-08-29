"""Deterministic local catalog seeds for the authored master studio.

The seed is deliberately explicit and owner-scoped.  It does not run at server
startup: operators invoke the companion script for the owner whose catalog is
being prepared.  Re-running it is a no-op while the published contract digest
is unchanged and creates a normal CAS revision when a fixture changes.
"""

from __future__ import annotations

import base64
from pathlib import Path
import re

from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import CreateDraftRequest, MasterCasRequest

from .assets import OwnerMasterAssetStore
from .digests import seal_master_contract
from .errors import MasterNotFoundError
from .service import AuthoredVNextMasterService


IBIZ_MASTER_ID = "master:ibiz-proposal-blue"
SAMPLE_MASTER_IDS = (
    "master:sample-clean-corporate",
    "master:sample-data-insight",
    "master:sample-dark-executive",
)
DEFAULT_IBIZ_LOGO_SVG = (
    Path.home() / "Documents" / "카카오톡 받은 파일" / "Ibiz_logo.svg"
)
_EMBEDDED_PNG = re.compile(
    r"data:image/png;base64,(?P<value>[A-Za-z0-9+/=\s]+)", re.IGNORECASE
)


def extract_embedded_png(svg_path: Path) -> bytes:
    """Extract the original PNG embedded in the supplied IBIZ SVG wrapper."""

    source = svg_path.read_text(encoding="utf-8")
    match = _EMBEDDED_PNG.search(source)
    if match is None:
        raise ValueError("ibiz_logo_svg_does_not_embed_png")
    try:
        return base64.b64decode("".join(match.group("value").split()), validate=True)
    except ValueError as error:
        raise ValueError("ibiz_logo_svg_embedded_png_invalid") from error


def publish_ibiz_logo(
    store: OwnerMasterAssetStore, svg_path: Path = DEFAULT_IBIZ_LOGO_SVG
) -> dict[str, object]:
    return store.publish_png(extract_embedded_png(svg_path))


def _bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x, "y": y, "width": width, "height": height}


def _ownership(*, locked: bool, writable: bool) -> dict[str, bool]:
    return {"locked": locked, "modelWritable": writable}


def _style(
    font_pt: float,
    minimum: float,
    lines: int,
    *,
    color: str = "textPrimary",
    weight: int = 600,
) -> dict[str, object]:
    return {
        "fontToken": "eastAsian",
        "colorToken": color,
        "fontPt": font_pt,
        "minFontPt": minimum,
        "lineHeight": 1.12,
        "fontWeight": weight,
        "maxLines": lines,
        "margins": {
            "leftPt": 7.2,
            "rightPt": 7.2,
            "topPt": 3.6,
            "bottomPt": 3.6,
        },
    }


def _layout(
    *,
    key: str,
    name: str,
    kind: str,
    title: tuple[float, float, float, float],
    body: tuple[float, float, float, float],
) -> dict[str, object]:
    ownership = _ownership(locked=False, writable=True)
    return {
        "id": f"layout:{key}",
        "name": name,
        "kind": kind,
        "slots": [
            {
                "id": f"slot:{key}-title",
                "role": "title",
                "placeholderIndex": 0,
                "bounds": _bounds(*title),
                "ownership": ownership,
                "style": "title",
            },
            {
                "id": f"slot:{key}-body",
                "role": "body",
                "placeholderIndex": 1,
                "bounds": _bounds(*body),
                "ownership": ownership,
                "style": "body",
            },
        ],
    }


def _base_contract(
    *,
    master_id: str,
    name: str,
    colors: dict[str, str],
    header: str,
    callout: str,
    logo: dict[str, object] | None = None,
) -> dict[str, object]:
    locked = _ownership(locked=True, writable=False)
    fallbacks = ["Noto Sans KR", "Arial", "sans-serif"]
    logo_reference = (
        {
            "assetId": logo["assetId"],
            "digest": logo["digest"],
            "altText": "IBIZ 로고",
        }
        if logo is not None
        else None
    )
    return {
        "contractVersion": "deckmaster-contract/v1",
        "masterId": master_id,
        "semanticVersion": "1.0.0",
        "name": name,
        "canvas": {
            "widthPx": 1280,
            "heightPx": 720,
            "locale": "ko-KR",
            "safeArea": _bounds(60, 76, 1160, 572),
        },
        "brandAssets": {"logo": logo_reference, "wordmark": None},
        "tokens": {
            "fonts": {
                "latin": "Malgun Gothic",
                "eastAsian": "Malgun Gothic",
                "complexScript": "Malgun Gothic",
                "fallbacks": fallbacks,
            },
            "colors": colors,
        },
        "fontManifest": {
            "primary": "Malgun Gothic",
            "fallbacks": fallbacks,
            "windowsFallback": "Malgun Gothic",
            "themeScripts": {
                "latin": "Malgun Gothic",
                "hang": "Malgun Gothic",
                "jpan": "Malgun Gothic",
                "hans": "Malgun Gothic",
                "hant": "Malgun Gothic",
            },
            "files": [],
        },
        "furniture": {
            "logo": {
                "id": "furniture:logo",
                "kind": "logo",
                "bounds": _bounds(1134, 675, 78, 38),
                "ownership": locked,
                "visibility": "always",
                "assetId": logo_reference["assetId"] if logo_reference else None,
            },
            "header": {
                "id": "furniture:header",
                "kind": "text",
                "bounds": _bounds(60, 30, 300, 26),
                "ownership": locked,
                "visibility": "always",
                "text": header,
                "fill": colors["accent"],
            },
            "footer": {
                "id": "furniture:footer",
                "kind": "text",
                "bounds": _bounds(925, 30, 295, 26),
                "ownership": locked,
                "visibility": "always",
                "text": callout,
                "fill": colors["textSecondary"],
            },
            "divider": {
                "id": "furniture:divider",
                "kind": "line",
                "bounds": _bounds(60, 662, 1160, 1),
                "ownership": locked,
                "visibility": "always",
                "stroke": colors["divider"],
            },
            "pageNumber": {
                "id": "furniture:page-number",
                "kind": "pageNumber",
                "bounds": _bounds(610, 680, 60, 22),
                "ownership": locked,
                "visibility": "exceptCover",
                "text": "1",
                "fill": colors["textSecondary"],
            },
        },
        "semanticStyles": {
            "eyebrow": _style(14, 12, 1, color="accent", weight=600),
            "title": _style(38, 26, 3, weight=700),
            "body": _style(20, 15, 8, color="textSecondary", weight=400),
            "note": _style(14, 11, 4, color="textSecondary", weight=400),
            "rightCallout": _style(18, 14, 5, color="accent", weight=600),
            "warning": _style(18, 14, 5, color="warning", weight=600),
            "caption": _style(12, 10, 3, color="textSecondary", weight=400),
            "source": _style(10, 8, 3, color="textSecondary", weight=400),
        },
        "layouts": {
            "cover": _layout(
                key="cover",
                name="표지",
                kind="cover",
                title=(92, 190, 1096, 158),
                body=(94, 382, 900, 112),
            ),
            "section": _layout(
                key="section",
                name="섹션",
                kind="section",
                title=(92, 186, 1096, 135),
                body=(94, 350, 900, 120),
            ),
            "standard": _layout(
                key="standard",
                name="본문",
                kind="standard",
                title=(92, 84, 1096, 112),
                body=(94, 226, 1092, 390),
            ),
            "longTitle": _layout(
                key="long-title",
                name="긴 제목",
                kind="longTitle",
                title=(92, 84, 1096, 176),
                body=(94, 294, 1092, 322),
            ),
        },
        "fitPolicy": {
            "preferredFontPt": 38,
            "minimumFontPt": 26,
            "standardMaxLines": 2,
            "longTitleMaxLines": 3,
            "promotionLayoutId": "layout:long-title",
            "explicitLineBreaks": True,
            "uax14Version": "17.0.0",
            "tailoring": "ko-space-first-with-id-fallback-v1",
            "kinsokuPolicy": "cjk-no-leading-closing-no-trailing-opening",
        },
        "titleStressCorpus": [
            {
                "id": "stress:ko",
                "category": "ko",
                "text": "검증된 제안서",
                "expectedLines": ["검증된 제안서"],
            },
            {
                "id": "stress:en",
                "category": "en",
                "text": "Clear proposal",
                "expectedLines": ["Clear proposal"],
            },
            {
                "id": "stress:mixed",
                "category": "mixed",
                "text": "IBIZ AI 제안",
                "expectedLines": ["IBIZ AI 제안"],
            },
            {
                "id": "stress:numeric",
                "category": "numericParenthetical",
                "text": "3단계 실행안 (2026)",
                "expectedLines": ["3단계 실행안 (2026)"],
            },
        ],
    }


def catalog_seed_contracts(
    ibiz_logo_receipt: dict[str, object],
) -> tuple[DeckMasterContract, ...]:
    ibiz = _base_contract(
        master_id=IBIZ_MASTER_ID,
        name="IBIZ 제안서 블루",
        header="IBIZ · BUSINESS PROPOSAL",
        callout="문서 코드 · CONFIDENTIAL",
        logo=ibiz_logo_receipt,
        colors={
            "background": "#FFFFFF",
            "surface": "#F5F8FC",
            "textPrimary": "#102A56",
            "textSecondary": "#6D7785",
            "accent": "#1473E6",
            "divider": "#D2D7DF",
            "warning": "#D64B55",
        },
    )
    samples = (
        _base_contract(
            master_id=SAMPLE_MASTER_IDS[0],
            name="클린 코퍼레이트",
            header="CORPORATE · PROPOSAL",
            callout="STRATEGY BRIEF",
            colors={
                "background": "#FFFFFF",
                "surface": "#F6F8FB",
                "textPrimary": "#172033",
                "textSecondary": "#5F6B7C",
                "accent": "#356AE6",
                "divider": "#D7DCE5",
                "warning": "#D64550",
            },
        ),
        _base_contract(
            master_id=SAMPLE_MASTER_IDS[1],
            name="데이터 인사이트",
            header="DATA · INSIGHT",
            callout="ANALYTICS REPORT",
            colors={
                "background": "#F8FBFC",
                "surface": "#FFFFFF",
                "textPrimary": "#12343B",
                "textSecondary": "#53747B",
                "accent": "#00A6A6",
                "divider": "#C7DDDF",
                "warning": "#E05A47",
            },
        ),
        _base_contract(
            master_id=SAMPLE_MASTER_IDS[2],
            name="다크 이그제큐티브",
            header="EXECUTIVE · BRIEF",
            callout="BOARD MATERIAL",
            colors={
                "background": "#101827",
                "surface": "#182337",
                "textPrimary": "#F4F7FB",
                "textSecondary": "#AAB7C8",
                "accent": "#F4C95D",
                "divider": "#39465B",
                "warning": "#FF6B6B",
            },
        ),
    )
    return tuple(
        DeckMasterContract.model_validate(payload)
        for payload in (ibiz, *samples)
    )


def seed_catalog(
    service: AuthoredVNextMasterService,
    asset_store: OwnerMasterAssetStore,
    *,
    ibiz_logo_svg: Path = DEFAULT_IBIZ_LOGO_SVG,
) -> list[dict[str, object]]:
    if ibiz_logo_svg.is_file():
        logo = publish_ibiz_logo(asset_store, ibiz_logo_svg)
    else:
        try:
            current_ibiz = service.get_revision(IBIZ_MASTER_ID)
        except MasterNotFoundError:
            current_ibiz = None
        current_logo = (
            current_ibiz.contract.brand_assets.logo
            if current_ibiz is not None
            else None
        )
        if current_logo is None:
            raise FileNotFoundError(
                "IBIZ logo source is missing and no catalog logo can be reused: "
                f"{ibiz_logo_svg}"
            )
        logo = current_logo.model_dump(by_alias=True)
    results: list[dict[str, object]] = []
    for contract in catalog_seed_contracts(logo):
        desired = seal_master_contract(contract)
        try:
            current = service.get_revision(contract.master_id)
        except MasterNotFoundError:
            current = None

        if (
            current is not None
            and current.status == "published"
            and current.contract.digest == desired.digest
        ):
            results.append(
                {
                    "masterId": current.master_id,
                    "revision": current.revision,
                    "digest": current.digest,
                    "action": "unchanged",
                }
            )
            continue

        if (
            current is not None
            and current.status == "draft"
            and current.contract.digest == desired.digest
        ):
            draft = current
        else:
            draft = service.create_draft(
                CreateDraftRequest(
                    contract=desired,
                    expected_revision=(current.revision if current else None),
                    expected_digest=(current.digest if current else None),
                )
            )
        published = service.publish(
            draft.master_id,
            MasterCasRequest(
                expected_revision=draft.revision,
                expected_digest=draft.digest,
            ),
        )
        results.append(
            {
                "masterId": published.master_id,
                "revision": published.revision,
                "digest": published.digest,
                "action": "published",
            }
        )
    return results


__all__ = [
    "DEFAULT_IBIZ_LOGO_SVG",
    "IBIZ_MASTER_ID",
    "SAMPLE_MASTER_IDS",
    "catalog_seed_contracts",
    "extract_embedded_png",
    "publish_ibiz_logo",
    "seed_catalog",
]
