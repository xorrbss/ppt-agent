"""Masked body renders of a template's exemplar pages: made at registration, sent to the author.

Design: docs/template-body-renders-design-20261105.md.

* **Registration** (:func:`attach_body_renders`, a framed registration with
  ``SVG_TEMPLATE_BODY_RENDERS`` on; off, the registration neither masks nor stores anything): each
  body exemplar page of the deck PDF is masked and cropped to its body
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

#: Renders held per process (about 100 KB each), keyed by (owner, tenant, sha256).
_CACHE_LIMIT = 64
_cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
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


def shared_libreoffice_pdf(source_path, package) -> tuple[str, bytes] | None:
    """The deck converted once by LibreOffice for the renders and the source review, or ``None`` when
    this server has no LibreOffice or the conversion fails (the source review then reports its own cause)."""
    from services.svg_templates.source_review import (
        LIBREOFFICE_RENDERER, convert_source_pdf_libreoffice, source_preview_renderer,
    )
    if source_preview_renderer() != LIBREOFFICE_RENDERER:
        return None
    try:
        return convert_source_pdf_libreoffice(Path(source_path), package)
    except Exception as error:  # noqa: BLE001 - the renders are skipped, the review retries on its own
        LOGGER.warning("[svg-template] deck PDF conversion failed: %s", type(error).__name__)
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
    """The package with its exemplars' masked renders stored and referenced:
    ``(package, artifact ids, receipt)``.

    Called by every framed registration. Returns the package unchanged (and no artifact) when nothing
    could be rendered; a page that fails, or whose render cannot be stored, is left without a render
    (each store in its own savepoint). The package digest is recomputed. The receipt (counts and the
    OCR path, never text) goes into the registration operation's result.
    """
    from models.svg_template_frame import SvgTemplateBodyRender
    from services.artifact_store import configured_public_artifact_service
    from services.svg_templates.catalog import principal
    from services.svg_templates.frame_fonts import artifact_digest
    from utils.get_env_concurrency import get_svg_template_body_render_excluded_pages_env

    composition = package.body_composition
    if composition is None or not composition.exemplars:
        return package, [], {"status": "no_exemplars"}
    try:
        renders, receipt = await asyncio.to_thread(
            render_exemplar_pages, package, Path(source_path), captured_pdf=captured_pdf,
            excluded_pages=get_svg_template_body_render_excluded_pages_env())
    except Exception as error:  # noqa: BLE001 - renders are guidance; registration goes on without them
        LOGGER.warning("[svg-template] body renders failed: %s", type(error).__name__)
        return package, [], {"status": "error", "reason": type(error).__name__}
    LOGGER.info("[svg-template] body renders: %s", receipt)
    if not renders:
        return package, [], receipt
    exemplars, artifact_ids, unstored = list(composition.exemplars), [], 0
    try:
        owner, tenant = principal()
        service = configured_public_artifact_service()
    except Exception as error:  # noqa: BLE001 - no store: the template is registered without renders
        LOGGER.warning("[svg-template] body render store unavailable: %s", type(error).__name__)
        return package, [], {**receipt, "rendered": 0, "unstored": len(renders)}
    for index, masked in sorted(renders.items()):
        try:
            async with session.begin_nested():
                ref = await service.persist_bytes(session, owner_id=owner, tenant_id=tenant, kind="image-derivative",
                                                  payload=masked.png, media_type="image/png",
                                                  created_by_operation_id=operation_id, retention_class="published")
        except Exception as error:  # noqa: BLE001 - one render that cannot be stored is left out
            LOGGER.warning("[svg-template] body render not stored: %s", type(error).__name__)
            unstored += 1
            continue
        artifact_ids.append(str(ref.artifact_id))
        exemplars[index] = exemplars[index].model_copy(update={"render": SvgTemplateBodyRender(
            artifact_id=str(ref.artifact_id), owner_id=str(ref.owner_id), tenant_id=ref.tenant_id,
            sha256=ref.sha256, width=masked.width, height=masked.height, mask=masked.mask_receipt())})
    receipt = {**receipt, "rendered": len(artifact_ids), **({"unstored": unstored} if unstored else {})}
    if not artifact_ids:
        return package, [], receipt
    updated = package.model_copy(update={
        "body_composition": composition.model_copy(update={"exemplars": tuple(exemplars)})})
    return updated.model_copy(update={"digest": artifact_digest(updated)}), artifact_ids, receipt


async def registration_body_renders(session, package, source_path, *, captured_pdf: bytes | None, operation_id):
    """The registration step: ``(package, artifact ids, receipt or None, shared LibreOffice PDF or None)``.

    Only with ``SVG_TEMPLATE_BODY_RENDERS`` on at registration (measured no effect on 2026-11-05, and
    the masking OCR costs minutes): off, nothing is converted, masked, read or stored, and the package
    comes back unchanged. On, one LibreOffice conversion (when no captured PDF exists) serves both the
    renders and the source review, and the renders are attached (:func:`attach_body_renders`).
    """
    from utils.get_env_concurrency import get_svg_template_body_renders_env

    if not get_svg_template_body_renders_env():
        return package, [], None, None
    libreoffice_pdf = None
    if captured_pdf is None:
        libreoffice_pdf = await asyncio.to_thread(shared_libreoffice_pdf, source_path, package)
    render_pdf = captured_pdf if captured_pdf is not None else (libreoffice_pdf[1] if libreoffice_pdf else None)
    package, artifact_ids, receipt = await attach_body_renders(session, package, source_path, captured_pdf=render_pdf,
                                                               operation_id=operation_id)
    return package, artifact_ids, receipt, libreoffice_pdf


# --- generation ---------------------------------------------------------------------------------


def _remember(key: tuple[str, str, str], payload: bytes) -> None:
    with _lock:
        _cache[key] = payload
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def cached_render(owner: str, tenant: str, sha256: str | None) -> bytes | None:
    """A loaded render, only under the owner and tenant it was loaded for."""
    if not sha256:
        return None
    with _lock:
        return _cache.get((str(owner), tenant, sha256))


def _current_principal() -> tuple[str, str] | None:
    from fastapi import HTTPException

    from services.svg_templates.catalog import principal
    try:
        owner, tenant = principal()
    except HTTPException:
        return None
    return str(owner), tenant


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
    owner with the recorded digest; it is cached under that owner and tenant. Never raises: a render
    that cannot be read is counted and left out.
    """
    from services.svg_templates.selection_material import template_package

    counts = {"loaded": 0, "cached": 0, "missing": 0, "scope_mismatch": 0}
    if not isinstance(selection, dict) or selection.get("mode") != "template-redesign":
        return counts
    try:
        composition = template_package(selection).get("body_composition") or {}
    except ValueError:
        return counts
    rendered = [exemplar["render"] for exemplar in composition.get("exemplars") or ()
                if isinstance(exemplar.get("render"), dict)]
    who = _current_principal()
    if who is None:
        # No owner in this context: no render can be proved to be the requester's.
        return {**counts, "scope_mismatch": len(rendered)}
    owner, tenant = who
    service = None
    for render in rendered:
        # Ownership first; only then the cache or the store.
        if render.get("owner_id") != owner or render.get("tenant_id") != tenant:
            counts["scope_mismatch"] += 1
            continue
        key = (owner, tenant, render.get("sha256"))
        if cached_render(*key) is not None:
            counts["cached"] += 1
            continue
        try:
            if service is None:
                from services.artifact_store import configured_public_artifact_service
                service = configured_public_artifact_service()
            current = await service.resolve_ref(session, artifact_id=uuid.UUID(render["artifact_id"]),
                owner_id=uuid.UUID(owner), tenant_id=tenant, expected_digest=render["sha256"], check_object=False)
            if current.kind != "image-derivative" or current.media_type != "image/png":
                raise ValueError("svg_template_body_render_kind_invalid")
            payload, _ = await service.read_verified_ref_bytes(current, max_bytes=8 * 1024 * 1024)
        except Exception as error:  # noqa: BLE001 - a render is guidance: a failed read is a missing render
            LOGGER.warning("[svg-template] body render unavailable: %s", type(error).__name__)
            counts["missing"] += 1
            continue
        if hashlib.sha256(payload).hexdigest() != render["sha256"]:
            counts["missing"] += 1
            continue
        _remember(key, payload)
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
    who = _current_principal()
    pages = []
    for index, exemplar in enumerate(chosen[:MAX_PAGE_RENDERS]):
        render = exemplar.get("render")
        if isinstance(render, dict):
            owned = who is not None and (render.get("owner_id"), render.get("tenant_id")) == who
            pages.append({"exemplar": index, "sha256": render.get("sha256"),
                          "png": cached_render(*who, render.get("sha256")) if owned else None})
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


__all__ = ["BODY_RENDER_RULE", "MAX_PAGE_RENDERS", "attach_body_renders", "registration_body_renders", "cached_render", "clear_render_cache",
           "ensure_body_renders", "page_body_renders", "render_exemplar_pages", "render_prompt_block",
           "render_receipt", "snapshot_body_renders"]
