"""stdin/stdout worker for the authored renderer boundary.

Two wire protocols are served on the same stdin, told apart from the first
eight bytes without a handshake (see ``utils.renderer_frame_codec``):

* **framed** -- ``<8-byte big-endian length><JSON>`` per request and per
  response. The process keeps one Chromium alive and serves requests until
  stdin EOF, ``max_requests`` renders, or the first failure. Each response
  carries ``request_ordinal`` alongside the fields the single-shot protocol
  already returns.
* **legacy single-shot** -- one bare JSON payload terminated by EOF, one
  response, exit. Preserved byte-for-byte for the pinned launcher and for any
  caller that has not moved to frames.

Production launches this module inside the network-none, non-root, read-only
renderer container.  The local command exists for development and focused
tests; ``AUTHORED_RENDERER_PRODUCTION_STRICT=1`` forbids that implicit local
fallback. Reusing the process never reuses renderer state: every request opens
a fresh browser context and target and disposes them before responding, and a
process only ever serves one caller-chosen deck scope.
"""

from __future__ import annotations

import asyncio
import base64
import json
import hashlib
import math
import re
import stat
from pathlib import Path
import os
import sys
from typing import Any, Mapping

from models.authored_vnext.capability_registry import capability_metadata
from utils.env_settings.authored_runtime import is_authored_renderer_worker_process
from utils.renderer_frame_codec import (
    DEFAULT_MAX_REQUESTS_PER_PROCESS,
    FRAME_HEADER_BYTES,
    clamp_max_requests,
    encode_frame,
    header_is_frame,
    read_frame_sync,
)
from utils.renderer_isolation import (
    MAX_HTML_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    RendererPolicyError,
    admit_authored_html,
    admit_static_output_html,
    assert_no_provider_secrets,
)
from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

RESPONSE_SCHEMA = "authored-render-response/v1"


def _validate_request(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RendererPolicyError("renderer request must be a JSON object")
    fields = set(value)
    if not {"schema_version", "html", "timeout"} <= fields or not fields <= {
        "schema_version",
        "html",
        "timeout",
        "max_requests",
        "width_px",
        "height_px",
        "preserved_raster_svg",
        "system_furniture",
        "font_manifest",
        "font_manifest_ref",
        "canonical_svg",
        "image_bindings",
        "fixed_glyph_preview_ref",
        "fixed_glyph_display_digest",
        "private_diagnostic_profile",
    }:
        raise RendererPolicyError("renderer request has an invalid shape")
    if value.get("schema_version") != "authored-render-request/v1":
        raise RendererPolicyError("renderer request schema is unsupported")
    html = value.get("html")
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("renderer HTML limit exceeded")
    has_font = "font_manifest" in fields or "font_manifest_ref" in fields
    has_glyph = "fixed_glyph_preview_ref" in fields or "fixed_glyph_display_digest" in fields
    if "private_diagnostic_profile" in fields and (
        value["private_diagnostic_profile"] != "fixed-glyph-dpr2/v1"
        or not has_glyph or "font_manifest_ref" not in fields
        or value.get("width_px") != 1280 or value.get("height_px") != 720
        or "preserved_raster_svg" in fields or "max_requests" in fields
    ):
        raise RendererPolicyError("renderer private diagnostic profile invalid")
    if has_glyph:
        ref = value.get("fixed_glyph_preview_ref")
        display = value.get("fixed_glyph_display_digest")
        if (not has_font or "canonical_svg" not in fields or "preserved_raster_svg" in fields
                or not isinstance(ref, dict) or set(ref) != {"name", "sha256"}
                or ref.get("name") != "fixed-glyph-preview.json"
                or not isinstance(ref.get("sha256"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", ref["sha256"]) is None
                or not isinstance(display, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", display) is None):
            raise RendererPolicyError("renderer fixed glyph binding invalid")
    if ("font_manifest" in fields and "font_manifest_ref" in fields
            or has_font and "preserved_raster_svg" in fields
            or "font_manifest" in fields and not isinstance(value["font_manifest"], dict)):
        raise RendererPolicyError("renderer font manifest invalid")
    if "font_manifest_ref" in fields:
        ref = value["font_manifest_ref"]
        if (not isinstance(ref, dict) or set(ref) != {"name", "sha256"}
                or ref.get("name") != "font-manifest.json"
                or not isinstance(ref.get("sha256"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", ref["sha256"]) is None):
            raise RendererPolicyError("renderer font manifest reference invalid")
    if "system_furniture" in fields:
        if not isinstance(value["system_furniture"], dict) or not (
                "preserved_raster_svg" in fields or has_font and {"canonical_svg", "image_bindings"} <= fields):
            raise RendererPolicyError("renderer furniture attestation requires an authorized SVG")
    if "canonical_svg" in fields:
        if (not has_font or not ({"system_furniture", "image_bindings"} <= fields or has_glyph)
                or "preserved_raster_svg" in fields
                or not isinstance(value["canonical_svg"], str)
                or len(value["canonical_svg"].encode("utf-8")) > 2 * 1024 * 1024):
            raise RendererPolicyError("renderer canonical SVG binding invalid")
    if "image_bindings" in fields and (not has_font or not {"system_furniture", "canonical_svg"} <= fields
            or not isinstance(value["image_bindings"], dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value["image_bindings"].items())):
        raise RendererPolicyError("renderer image bindings invalid")
    if "preserved_raster_svg" in fields:
        raster = value["preserved_raster_svg"]
        if not isinstance(raster, str) or len(raster.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise RendererPolicyError("renderer raster derivative limit exceeded")
    timeout = value.get("timeout")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
        raise RendererPolicyError("renderer timeout must be between 1 and 120 seconds")
    if "max_requests" in fields:
        clamp_max_requests(value["max_requests"])
    for field_name, default in (("width_px", DEFAULT_CANVAS_WIDTH_PX), ("height_px", DEFAULT_CANVAS_HEIGHT_PX)):
        dimension = value.get(field_name, default)
        if (
            not isinstance(dimension, int)
            or isinstance(dimension, bool)
            or not 1 <= dimension <= 4096
        ):
            raise RendererPolicyError("renderer canvas dimensions are invalid")
    return value


def _parse_request(payload: bytes) -> Mapping[str, Any]:
    if not payload or len(payload) > MAX_REQUEST_BYTES:
        raise RendererPolicyError("renderer request wire limit exceeded")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RendererPolicyError("renderer request is not valid JSON") from exc
    return _validate_request(value)


def _read_legacy_request(prefix: bytes) -> Mapping[str, Any]:
    payload = prefix + sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1 - len(prefix))
    request = _parse_request(payload)
    if "max_requests" in request:
        raise RendererPolicyError("renderer request has an invalid shape")
    return request


def _read_font_manifest(ref: dict, assets_root: Path | None) -> dict:
    if assets_root is None or not assets_root.is_absolute() or not assets_root.is_dir():
        raise RendererPolicyError("renderer font assets root unavailable")
    root = assets_root.resolve(strict=True)
    path = root / "font-manifest.json"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 90 * 1024 * 1024:
            raise RendererPolicyError("renderer font manifest asset invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != info.st_size:
                raise RendererPolicyError("renderer font manifest asset invalid")
            payload = stream.read(90 * 1024 * 1024 + 1)
    except OSError as error:
        raise RendererPolicyError("renderer font manifest asset unavailable") from error
    if len(payload) > 90 * 1024 * 1024 or "sha256:" + hashlib.sha256(payload).hexdigest() != ref["sha256"]:
        raise RendererPolicyError("renderer font manifest digest mismatch")
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise RendererPolicyError("renderer font manifest asset invalid") from error
    if not isinstance(result, dict):
        raise RendererPolicyError("renderer font manifest asset invalid")
    from services.svg_templates.frame_fonts import MAX_FONT_BYTES, resolve_font_manifest
    if sum(len(content) for _asset, content in resolve_font_manifest(result)) > MAX_FONT_BYTES:
        raise RendererPolicyError("renderer font manifest byte limit exceeded")
    return result


def _read_fixed_glyph_preview(ref: dict, assets_root: Path | None) -> dict:
    if assets_root is None or not assets_root.is_absolute() or not assets_root.is_dir():
        raise RendererPolicyError("renderer glyph assets root unavailable")
    root = assets_root.resolve(strict=True)
    path = root / "fixed-glyph-preview.json"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 * 1024:
            raise RendererPolicyError("renderer glyph asset invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != info.st_size:
                raise RendererPolicyError("renderer glyph asset invalid")
            payload = stream.read(16 * 1024 * 1024 + 1)
    except OSError as error:
        raise RendererPolicyError("renderer glyph asset unavailable") from error
    if len(payload) > 16 * 1024 * 1024 or "sha256:" + hashlib.sha256(payload).hexdigest() != ref["sha256"]:
        raise RendererPolicyError("renderer glyph asset digest mismatch")
    try:
        value = json.loads(payload)
        from models.svg_template_glyph import SvgTemplateFixedGlyphPreview
        return SvgTemplateFixedGlyphPreview.model_validate(value).model_dump(mode="json")
    except (ValueError, UnicodeDecodeError) as error:
        raise RendererPolicyError("renderer glyph asset invalid") from error


def _verify_fixed_glyph_dom(metrics: dict, projected_svg: str, width: int, height: int) -> dict:
    """Compare Chromium's actual inert path DOM with the sealed projection."""
    from lxml import etree

    raw = metrics.pop("fixed_glyph_dom", None)
    if (not isinstance(raw, list) or not raw or len(raw) > 200
            or len(json.dumps(raw, separators=(",", ":"))) > 4 * 1024 * 1024):
        raise RendererPolicyError("renderer glyph DOM evidence missing")
    root = etree.fromstring(projected_svg.encode(), parser=etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True))
    expected = [node for node in root.iter()
                if node.get("data-presenton-fixed-glyph") == "v1"]
    if len(expected) != len(raw):
        raise RendererPolicyError("renderer glyph DOM group mismatch")
    total_paths = 0
    bounded = []
    for group, observed in zip(expected, raw, strict=True):
        if (not isinstance(observed, dict) or observed.get("id") != group.get("id")
                or observed.get("qid") != group.get("data-qid")
                or observed.get("childCount") != len(group)
                or not isinstance(observed.get("paths"), list)
                or len(observed["paths"]) != len(group)):
            raise RendererPolicyError("renderer glyph DOM group mismatch")
        chain = observed.get("chain")
        if not isinstance(chain, list) or len(chain) < 3 or len(chain) > 20:
            raise RendererPolicyError("renderer glyph DOM context invalid")
        for state in chain:
            if (not isinstance(state, dict) or state.get("display") == "none"
                    or state.get("visibility") != "visible"
                    or state.get("opacity") != "1"
                    or state.get("transform") != "none"
                    or state.get("filter") != "none"
                    or state.get("clipPath") != "none"
                    or state.get("maskImage") != "none"
                    or state.get("mixBlendMode") != "normal"):
                raise RendererPolicyError("renderer glyph DOM effect unsupported")
        bounds = observed.get("bounds")
        if (not isinstance(bounds, dict) or any(not isinstance(bounds.get(k), (int, float))
                or not math.isfinite(bounds[k]) for k in ("x", "y", "width", "height"))
                or bounds["width"] <= 0 or bounds["height"] <= 0
                or bounds["x"] < 0 or bounds["y"] < 0
                or bounds["x"] + bounds["width"] > width
                or bounds["y"] + bounds["height"] > height):
            raise RendererPolicyError("renderer glyph DOM geometry invalid")
        for path, dom in zip(group, observed["paths"], strict=True):
            if (not isinstance(dom, dict) or path.tag != "{http://www.w3.org/2000/svg}path"
                    or dom.get("tag") != "path" or dom.get("id") != path.get("id")
                    or dom.get("d") != path.get("d") or dom.get("fill") != path.get("fill")
                    or dom.get("computedStroke") != "none"
                    or dom.get("computedOpacity") != "1"
                    or dom.get("computedVisibility") != "visible"):
                raise RendererPolicyError("renderer glyph DOM path mismatch")
            rgb = tuple(bytes.fromhex(path.get("fill", "")[1:]))
            if dom.get("computedFill") != f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})":
                raise RendererPolicyError("renderer glyph DOM paint mismatch")
            box = dom.get("bbox")
            if (not isinstance(box, dict) or any(not isinstance(box.get(k), (int, float))
                    or not math.isfinite(box[k]) for k in ("x", "y", "width", "height"))
                    or box["width"] <= 0 or box["height"] <= 0):
                raise RendererPolicyError("renderer glyph DOM path geometry invalid")
            total_paths += 1
        bounded.append({"id": group.get("id"), "qid": group.get("data-qid"),
                        "pathCount": len(group), "bounds": bounds})
    proof = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"groupCount": len(expected), "pathCount": total_paths,
            "domEvidenceDigest": "sha256:" + hashlib.sha256(proof).hexdigest(),
            "groups": bounded}


async def _render(
    request: Mapping[str, Any], session: Any = None, *, ordinal: int | None = None,
    assets_root: Path | None = None,
) -> Mapping[str, Any]:
    # Import only after the worker environment and wire request are validated.  This
    # also prevents importing the network author/provider layer in the renderer.
    from utils import slide_capture

    source = str(request["html"])
    timeout = int(request["timeout"])
    width_px = int(request.get("width_px", DEFAULT_CANVAS_WIDTH_PX))
    height_px = int(request.get("height_px", DEFAULT_CANVAS_HEIGHT_PX))
    diagnostic_profile = request.get("private_diagnostic_profile")
    glyph_result = None
    if request.get("preserved_raster_svg") is not None:
        from services.svg_renderer import admit_preserved_raster_preview
        admitted, _ = admit_preserved_raster_preview(
            source, request["preserved_raster_svg"], width=width_px, height=height_px,
            **({"system_furniture": request["system_furniture"]} if request.get("system_furniture") is not None else {}))
    else:
        try:
            admitted = admit_authored_html(source)
        except RendererPolicyError:
            admitted = admit_static_output_html(source)
        from services.svg_renderer import materialize_trusted_svg_fonts
        font_manifest = (request.get("font_manifest") if request.get("font_manifest_ref") is None
                         else _read_font_manifest(request["font_manifest_ref"], assets_root))
        if font_manifest is not None:
            from services.svg_renderer import materialize_template_manifest_fonts, validate_template_render_furniture
            if request.get("system_furniture") is not None:
                validate_template_render_furniture(source, request["canonical_svg"], request["system_furniture"],
                                                   width=width_px, height=height_px,
                                                   image_bindings=request["image_bindings"])
            admitted = materialize_template_manifest_fonts(source, admitted, font_manifest)
            if request.get("fixed_glyph_preview_ref") is not None:
                from services.svg_renderer import materialize_template_fixed_glyphs
                authority = _read_fixed_glyph_preview(request["fixed_glyph_preview_ref"], assets_root)
                admitted, glyph_result = materialize_template_fixed_glyphs(
                    admitted, request["canonical_svg"], authority,
                    expected_display_digest=request["fixed_glyph_display_digest"],
                    font_manifest=font_manifest)
        else:
            admitted = materialize_trusted_svg_fonts(source, admitted)
    if session is None:
        png, metrics = await slide_capture._render_admitted_html_in_worker(
            admitted, timeout=timeout, width_px=width_px, height_px=height_px,
            private_diagnostic_profile=diagnostic_profile,
        )
    else:
        png, metrics = session.render(
            admitted, timeout, width_px=width_px, height_px=height_px,
            private_diagnostic_profile=diagnostic_profile,
        )
    metrics = dict(metrics)
    if glyph_result is not None:
        dom_proof = _verify_fixed_glyph_dom(metrics, glyph_result.svg, width_px, height_px)
        metrics["fixedGlyphPreview"] = {
            "displayDigest": glyph_result.display_digest,
            "renderDigest": glyph_result.render_digest,
            "projectionDigest": glyph_result.projection_digest,
            "fontCssDigest": glyph_result.trusted_font_css_digest,
            "authorityDigest": glyph_result.authority_digest,
            "materializerCodeDigest": glyph_result.materializer_code_digest,
            **dom_proof,
        }
        if diagnostic_profile is not None:
            raster = metrics.get("privateDiagnosticRaster")
            if not isinstance(raster, dict):
                raise RendererPolicyError("renderer private diagnostic raster missing")
            raster.update({
                "canonicalDigest": glyph_result.canonical_digest,
                "displayDigest": glyph_result.display_digest,
                "renderDigest": glyph_result.render_digest,
                "projectionDigest": glyph_result.projection_digest,
                "fontCssDigest": glyph_result.trusted_font_css_digest,
                "authorityDigest": glyph_result.authority_digest,
                "materializerCodeDigest": glyph_result.materializer_code_digest,
                "pngDigest": "sha256:" + hashlib.sha256(png).hexdigest(),
            })
    metrics["capabilityRegistry"] = capability_metadata(consumer="chromium")
    response: dict[str, Any] = {
        "schema_version": RESPONSE_SCHEMA,
        "ok": True,
        "png_base64": base64.b64encode(png).decode("ascii"),
        "metrics": metrics,
        "source_digest": admitted.source_digest,
        "asset_digests": list(admitted.asset_digests),
    }
    if ordinal is not None:
        response["request_ordinal"] = ordinal
    return response


def _encode_response(value: Mapping[str, Any], *, framed: bool) -> bytes:
    body = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(body) + (FRAME_HEADER_BYTES if framed else 1) > MAX_RESPONSE_BYTES:
        replacement: dict[str, Any] = {
            "schema_version": RESPONSE_SCHEMA,
            "ok": False,
            "error": "renderer response limit exceeded",
        }
        if "request_ordinal" in value:
            replacement["request_ordinal"] = value["request_ordinal"]
        body = json.dumps(replacement, separators=(",", ":")).encode("utf-8")
    if framed:
        return encode_frame(body, limit=MAX_RESPONSE_BYTES - FRAME_HEADER_BYTES)
    return body + b"\n"


def _write_response(value: Mapping[str, Any], *, framed: bool = False) -> None:
    sys.stdout.buffer.write(_encode_response(value, framed=framed))
    sys.stdout.buffer.flush()


def _failure_response(exc: BaseException, ordinal: int | None) -> dict[str, Any]:
    # Only the exception class and bounded policy message cross the boundary;
    # request HTML, environment values, paths, and Chrome stderr never do.
    message = str(exc).replace("\r", " ").replace("\n", " ")[:256]
    response: dict[str, Any] = {
        "schema_version": RESPONSE_SCHEMA,
        "ok": False,
        "error_type": type(exc).__name__,
        "error": message or "authored renderer failed",
    }
    if ordinal is not None:
        response["request_ordinal"] = ordinal
    return response


def _serve_framed(first_header: bytes) -> int:
    """Serve framed requests over one Chromium until EOF, the ceiling, or a failure."""

    from utils.authored_renderer_session import RendererChromiumSession

    session = RendererChromiumSession()
    max_requests = DEFAULT_MAX_REQUESTS_PER_PROCESS
    ordinal = 0
    header: bytes | None = first_header
    try:
        while ordinal < max_requests:
            payload = read_frame_sync(
                sys.stdin.buffer.read, limit=MAX_REQUEST_BYTES, header=header
            )
            header = None
            if payload is None:
                return 0
            ordinal += 1
            request = _parse_request(payload)
            if ordinal == 1 and "max_requests" in request:
                max_requests = clamp_max_requests(request["max_requests"])
            response = asyncio.run(_render(request, session, ordinal=ordinal))
            _write_response(response, framed=True)
        return 0
    except Exception as exc:
        # A failed exchange leaves no guarantee that this Chromium is still
        # usable, so the process stops after reporting it rather than serving
        # the next slide from a possibly poisoned browser.  The parent retires
        # the session on the same signal and opens a fresh worker.
        _write_response(_failure_response(exc, ordinal or None), framed=True)
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    assets_root = None
    if arguments:
        if len(arguments) != 2 or arguments[0] != "--request-assets-root":
            raise RendererPolicyError("renderer worker argument invalid")
        assets_root = Path(arguments[1])
    framed = False
    try:
        if not is_authored_renderer_worker_process():
            raise RendererPolicyError("renderer worker boundary marker is missing")
        assert_no_provider_secrets(os.environ)
        header = sys.stdin.buffer.read(FRAME_HEADER_BYTES)
        if header_is_frame(header, limit=MAX_REQUEST_BYTES):
            if assets_root is not None:
                raise RendererPolicyError("renderer font assets require single-shot worker")
            framed = True
            return _serve_framed(header)
        request = _read_legacy_request(header)
        _write_response(asyncio.run(_render(request, assets_root=assets_root)))
        return 0
    except Exception as exc:
        _write_response(_failure_response(exc, None), framed=framed)
        return 1
    finally:
        module = sys.modules.get("utils.slide_capture")
        if module is not None:
            try:
                module._close_reusable_chrome()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
