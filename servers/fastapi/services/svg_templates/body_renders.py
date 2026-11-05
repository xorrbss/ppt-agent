"""Masked body renders of a template's exemplar pages: made at registration, sent to the author.

Design: docs/template-body-renders-design-20261105.md.

* **Registration** (:func:`attach_body_renders`, every framed registration, whatever the
  generation knob says): each body exemplar page of the deck PDF is masked and cropped to its body
  region (``services/svg_templates/body_render_mask.py``), stored as an owner-bound ``published``
  artifact and referenced from the exemplar (``SvgTemplateBodyRender``: ids, a digest, sizes and the
  mask receipt; no text). A page whose masking cannot be verified gets no render; registration goes
  on. ``SVG_TEMPLATE_BODY_RENDER_EXCLUDED_PAGES`` withholds given source pages (measurement only).
* **Generation**: ``SVG_TEMPLATE_BODY_RENDERS`` is frozen into a template-redesign snapshot at
  admission (``template_body_renders``). :func:`ensure_body_renders` loads the renders of such a
  deck's template into this process, owner-scoped: a render is read only when its artifact belongs
  to the requesting principal, the owner of the template pin. :func:`page_body_renders` gives a body
  page the renders of the one or two exemplars it already gets as outlines. A missing render sends
  no image and is counted; the page proceeds as before (fail open).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path
from threading import Lock

LOGGER = logging.getLogger(__name__)

#: Renders held per process (about 100 KB each).
_CACHE_LIMIT = 64
_cache: OrderedDict[str, bytes] = OrderedDict()
_lock = Lock()
#: A body page gets at most this many renders (the exemplars it gets as outlines).
MAX_PAGE_RENDERS = 2
_SLIDE_ID = re.compile(rb"<p:sldId\b")


# --- registration -------------------------------------------------------------------------------


def _slide_count(source_path: Path) -> int | None:
    """Slides listed in the deck's presentation part (hidden ones included)."""
    try:
        with zipfile.ZipFile(source_path) as archive:
            return len(_SLIDE_ID.findall(archive.read("ppt/presentation.xml")))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def render_exemplar_pages(package, source_path: Path, *, captured_pdf: bytes | None = None,
                          excluded_pages: frozenset[int] = frozenset(),
                          ocr=None, ocr_name: str | None = None) -> tuple[dict[int, object], dict]:
    """``({exemplar index: MaskedRender}, receipt)`` for the package's body exemplars.

    Blocking (LibreOffice, PDFium, OCR): run it in a thread. Never raises for a page; a deck that
    cannot be converted gives no render and says why in the receipt. ``excluded_pages`` are 1-based
    slide numbers. ``ocr``/``ocr_name`` replace the product OCR runtime (tests).
    """
    from services.svg_templates.body_render_mask import BodyRenderRejected, mask_pdf_page, resolve_ocr_reader

    composition = package.body_composition
    exemplars = tuple(composition.exemplars) if composition is not None else ()
    receipt: dict = {"exemplars": len(exemplars), "rendered": 0, "excluded": 0, "rejected": {}}
    if not exemplars:
        return {}, {**receipt, "status": "no_exemplars"}
    pdf = captured_pdf
    if pdf is None:
        from services.svg_templates.source_review import convert_source_pdf_libreoffice
        try:
            _version, pdf = convert_source_pdf_libreoffice(Path(source_path), package)
        except (ValueError, OSError) as error:
            return {}, {**receipt, "status": "pdf_unavailable", "reason": str(error)[:80]}
        except Exception as error:  # noqa: BLE001 - a conversion failure never fails a registration
            return {}, {**receipt, "status": "pdf_unavailable", "reason": type(error).__name__}
    if ocr is None and ocr_name is None:
        ocr, ocr_name = resolve_ocr_reader()
    receipt["ocr"] = ocr_name if ocr is not None else "unavailable"
    expected = _slide_count(Path(source_path))
    renders = {}
    for index, exemplar in enumerate(exemplars):
        if exemplar.source_page + 1 in excluded_pages:
            receipt["excluded"] += 1
            continue
        if exemplar.region_x is None or exemplar.region_y is None:
            receipt["rejected"]["region_unknown"] = receipt["rejected"].get("region_unknown", 0) + 1
            continue
        try:
            renders[index] = mask_pdf_page(
                pdf, exemplar.source_page, canvas_width_px=package.source_canvas.width_px,
                crop=(exemplar.region_x, exemplar.region_y, exemplar.region_width, exemplar.region_height),
                ocr=ocr, ocr_name=ocr_name or "unavailable", expected_pages=expected)
        except BodyRenderRejected as error:
            code = str(error).removeprefix("svg_template_body_render_")
            receipt["rejected"][code] = receipt["rejected"].get(code, 0) + 1
        except Exception as error:  # noqa: BLE001 - one page's failure never fails a registration
            LOGGER.warning("[svg-template] body render of page %s failed: %s", exemplar.source_page,
                           type(error).__name__)
            receipt["rejected"]["error"] = receipt["rejected"].get("error", 0) + 1
    receipt.update(status="rendered", rendered=len(renders))
    return renders, receipt


async def attach_body_renders(session, package, source_path, *, captured_pdf: bytes | None, operation_id):
    """The package with its exemplars' masked renders stored and referenced: ``(package, artifact ids)``.

    Called by every framed registration. Returns the package unchanged (and no artifact) when nothing
    could be rendered; a page that fails is left without a render. The package digest is recomputed.
    """
    from models.svg_template_frame import SvgTemplateBodyRender
    from services.artifact_store import configured_public_artifact_service
    from services.svg_templates.catalog import principal
    from services.svg_templates.frame_fonts import artifact_digest
    from utils.get_env_concurrency import get_svg_template_body_render_excluded_pages_env

    composition = package.body_composition
    if composition is None or not composition.exemplars:
        return package, []
    try:
        renders, receipt = await asyncio.to_thread(
            render_exemplar_pages, package, Path(source_path), captured_pdf=captured_pdf,
            excluded_pages=get_svg_template_body_render_excluded_pages_env())
    except Exception as error:  # noqa: BLE001 - renders are guidance; registration goes on without them
        LOGGER.warning("[svg-template] body renders failed: %s", type(error).__name__)
        return package, []
    LOGGER.info("[svg-template] body renders: %s", receipt)
    if not renders:
        return package, []
    owner, tenant = principal()
    service = configured_public_artifact_service()
    exemplars, artifact_ids = list(composition.exemplars), []
    for index, masked in sorted(renders.items()):
        ref = await service.persist_bytes(session, owner_id=owner, tenant_id=tenant, kind="image-derivative",
                                          payload=masked.png, media_type="image/png",
                                          created_by_operation_id=operation_id, retention_class="published")
        artifact_ids.append(str(ref.artifact_id))
        exemplars[index] = exemplars[index].model_copy(update={"render": SvgTemplateBodyRender(
            artifact_id=str(ref.artifact_id), owner_id=str(ref.owner_id), tenant_id=ref.tenant_id,
            sha256=ref.sha256, width=masked.width, height=masked.height, mask=masked.mask_receipt())})
    updated = package.model_copy(update={
        "body_composition": composition.model_copy(update={"exemplars": tuple(exemplars)})})
    return updated.model_copy(update={"digest": artifact_digest(updated)}), artifact_ids


# --- generation ---------------------------------------------------------------------------------


def _remember(sha256: str, payload: bytes) -> None:
    with _lock:
        _cache[sha256] = payload
        _cache.move_to_end(sha256)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def cached_render(sha256: str | None) -> bytes | None:
    if not sha256:
        return None
    with _lock:
        return _cache.get(sha256)


def clear_render_cache() -> None:
    """Tests only."""
    with _lock:
        _cache.clear()


def snapshot_body_renders(snapshot: dict | None) -> bool:
    """Whether a frozen snapshot was admitted with ``SVG_TEMPLATE_BODY_RENDERS`` on."""
    return (snapshot or {}).get("template_body_renders") is True


async def ensure_body_renders(session, selection: dict | None) -> dict:
    """Load the masked renders of a template-redesign selection's package into this process.

    Owner-scoped: a render is read only when its recorded owner and tenant are the requesting
    principal (the owner the template pin was resolved for) and the artifact store finds it under that
    owner with the recorded digest. Never raises: a render that cannot be read is counted and left out.
    """
    from fastapi import HTTPException

    from services.artifact_store import ArtifactStoreError, configured_public_artifact_service
    from services.svg_templates.catalog import principal
    from services.svg_templates.selection_material import template_package

    counts = {"loaded": 0, "cached": 0, "missing": 0, "scope_mismatch": 0}
    if not isinstance(selection, dict) or selection.get("mode") != "template-redesign":
        return counts
    try:
        composition = template_package(selection).get("body_composition") or {}
    except ValueError:
        return counts
    try:
        owner, tenant = principal()
    except HTTPException:
        # No owner in this context: no render can be proved to be the requester's.
        return {**counts, "scope_mismatch": sum(isinstance(item.get("render"), dict)
                                                for item in composition.get("exemplars") or ())}
    service = configured_public_artifact_service()
    for exemplar in composition.get("exemplars") or ():
        render = exemplar.get("render")
        if not isinstance(render, dict):
            continue
        if cached_render(render.get("sha256")) is not None:
            counts["cached"] += 1
            continue
        if render.get("owner_id") != str(owner) or render.get("tenant_id") != tenant:
            counts["scope_mismatch"] += 1
            continue
        try:
            current = await service.resolve_ref(session, artifact_id=uuid.UUID(render["artifact_id"]),
                owner_id=owner, tenant_id=tenant, expected_digest=render["sha256"], check_object=False)
            if current.kind != "image-derivative" or current.media_type != "image/png":
                raise ValueError("svg_template_body_render_kind_invalid")
            payload, _ = await service.read_verified_ref_bytes(current, max_bytes=8 * 1024 * 1024)
        except (ArtifactStoreError, ValueError, KeyError) as error:
            LOGGER.warning("[svg-template] body render unavailable: %s", type(error).__name__)
            counts["missing"] += 1
            continue
        if hashlib.sha256(payload).hexdigest() != render["sha256"]:
            counts["missing"] += 1
            continue
        _remember(render["sha256"], payload)
        counts["loaded"] += 1
    return counts


def page_body_renders(snapshot: dict, context: dict | None, content: str, *,
                      source_preserved: bool = False) -> list[dict]:
    """The renders for one body page: ``[{"exemplar": k, "sha256", "png" (None when missing)}]``.

    Empty unless the snapshot was admitted with renders on, the page is a body page of a
    template-redesign deck that gets exemplar outlines, and the template stores renders. ``exemplar``
    is the page's ``template_exemplars`` index the render shows.
    """
    from services.svg_templates.generation import page_frame, template_selection
    from services.svg_templates.selection_material import template_package
    from utils.get_env_concurrency import get_svg_template_body_exemplars_env

    if not context or source_preserved or not snapshot_body_renders(snapshot):
        return []
    if get_svg_template_body_exemplars_env() != "on":
        return []
    if page_frame(snapshot, context["page_index"], context["page_count"]).role != "body":
        return []
    from services.svg_templates.body_composition import choose_exemplars
    chosen = choose_exemplars(template_package(template_selection(snapshot)).get("body_composition"), content)
    pages = []
    for index, exemplar in enumerate(chosen[:MAX_PAGE_RENDERS]):
        render = exemplar.get("render")
        if isinstance(render, dict):
            pages.append({"exemplar": index, "sha256": render.get("sha256"),
                          "png": cached_render(render.get("sha256"))})
    return pages


#: The rule sentence added to a template-redesign author's rules when the deck has renders on.
BODY_RENDER_RULE = (
    "\nTEMPLATE BODY RENDERS, when given, are pictures of this template's own body pages with every text"
    " line replaced by a solid bar (bar height shows the type size, its colour the text's role); each is"
    " bound to one template_exemplars entry. Match their fill, scale, density, component mix and the"
    " placement of figures and pictures with this page's own content. They are not content and not a"
    " layout to copy: never copy their positions, counts or arrangement, never draw a bar, and never"
    " invent text, figures or items to fill a shape. Their pictures belong to the template; place only"
    " offered image slots.\n"
)


def render_prompt_block(entries: list[dict], first_image_index: int) -> str:
    """The user-message block that binds each sent render image to its exemplar."""
    import json

    sent = [entry for entry in entries if entry.get("png")]
    return "\n\nTEMPLATE BODY RENDERS (masked pages of this template; layout evidence, not content):\n" + json.dumps({
        "template_body_renders": [{"image_index": first_image_index + position, "template_exemplar": entry["exemplar"]}
                                  for position, entry in enumerate(sent)]}, ensure_ascii=False)


def render_receipt(entries: list[dict]) -> dict:
    sent = [entry for entry in entries if entry.get("png")]
    return {"offered": len(entries), "sent": len(sent), "missing": len(entries) - len(sent),
            "digests": [entry["sha256"] for entry in sent]}


__all__ = ["BODY_RENDER_RULE", "MAX_PAGE_RENDERS", "attach_body_renders", "cached_render", "clear_render_cache",
           "ensure_body_renders", "page_body_renders", "render_exemplar_pages", "render_prompt_block",
           "render_receipt", "snapshot_body_renders"]
