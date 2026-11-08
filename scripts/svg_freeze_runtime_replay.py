"""Validate genuine synthetic SVG captures; explicitly use the existing principal/publication service.

Default mode has no database, authentication, rendering, network or provider calls.
Resolve/publish modes require the unchanged managed PostgreSQL and normal auth
environment. They neither export nor create jobs, admissions, reviews or endpoints.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "servers" / "fastapi"
sys.path.insert(0, str(BACKEND))
SCHEMA = "svg-freeze-runtime-replay/v1"
V5 = "svg-native-requirements/v5"
CHART_TYPES = {"bar", "column", "line", "area", "pie", "donut"}
CODE_INPUTS = {
    "servers/fastapi/services/svg_renderer.py",
    "servers/fastapi/services/generation_core/drivers/svg/native_chart_evidence.py",
    "servers/fastapi/services/generation_core/drivers/svg/native_requirements.py",
    "servers/fastapi/services/generation_core/drivers/svg/chart_content_paint.py",
}
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
DERIVED_QUALITY_FIELDS = frozenset({"verified", "classification", "requestedNativeObjects"})
PRODUCER_QUALITY_FIELDS = frozenset({
    "contract_version", "renderer_version", "source_digest", "png_digest", "wrapper_digest",
    "metrics_digest", "measurement_complete", "geometry_passed", "passed", "verified",
    "semantic_review", "failures", "body_font_floor_px", "browser_metrics", "overlap_basis",
    "trusted_font_materialization", "physical_readability",
})


class ReplayInputError(ValueError):
    """Only content-free error codes may cross this script's output boundary."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise ReplayInputError(code)


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def json_bytes(payload: bytes) -> Any:
    def reject_constant(_value):
        raise ReplayInputError("nonfinite_json_input")

    return json.loads(payload, parse_constant=reject_constant)


def read_limited(path: Path, limit: int = 128 * 1024 * 1024) -> bytes:
    require(path.is_file() and not path.is_symlink(), "input_file_required")
    require(path.stat().st_size <= limit, "input_file_too_large")
    return path.read_bytes()


def referenced(base: Path, ref: dict, *, as_json: bool = True):
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "file_reference_invalid")
    require(isinstance(ref["path"], str) and not Path(ref["path"]).is_absolute(), "relative_input_path_required")
    path = (base / ref["path"]).resolve()
    require(path.is_relative_to(base), "input_path_outside_manifest_directory")
    payload = read_limited(path)
    require(isinstance(ref["sha256"], str) and _HASH.fullmatch(ref["sha256"])
            and digest(payload) == ref["sha256"], "input_file_digest_mismatch")
    return json_bytes(payload) if as_json else payload.decode("utf-8")


@dataclass(frozen=True)
class ValidatedReplay:
    request: Any
    outline: Any
    snapshot: dict
    roles: list[str]
    pages: list[tuple[dict, bytes]]
    quality_summary: dict
    provenance: dict


def validate_inputs(path: Path) -> ValidatedReplay:
    # These imports are limited to typed requests and the real pure consumers.
    # Database/auth/publication imports are confined to explicit publish mode.
    from models.generate_presentation_request import GeneratePresentationRequest
    from models.presentation_outline_model import PresentationOutlineModel
    from services.authored_canvas import request_canvas_metadata
    from services.document_canvas import parse_canvas_metadata
    from services.document_canvas_generation import (
        generation_font_floor, svg_readability_font_floor, validate_svg_readability_policy,
        validate_svg_readability_publication,
    )
    from services.generation_core.drivers.svg.chart_content_paint import validate_chart_content_paint
    from services.generation_core.drivers.svg.native_chart_evidence import build_chart_witness_manifest
    from services.generation_core.drivers.svg.native_declarations import bound_declarations, native_binding_digest
    from services.generation_core.drivers.svg.native_publish import canvas_point_per_px, page_native_objects
    from services.generation_core.drivers.svg.native_requirements import apply_requested_native_quality, requirements_revision
    from services.generation_core.drivers.svg.quality import reclassify_pages
    from services.svg_contract import finalize_svg, source_digest, validate_svg
    from services.svg_renderer import SvgRenderResult
    from services.svg_authoring_profile import validate_profile_agreement, validate_quality_recovery_agreement
    from utils.hashing import canonical_json_digest

    base = path.resolve().parent
    raw_manifest = read_limited(path.resolve(), 4 * 1024 * 1024)
    manifest = json_bytes(raw_manifest)
    require(isinstance(manifest, dict) and manifest.get("schema") == SCHEMA
            and manifest.get("synthetic") is True, "synthetic_replay_manifest_required")
    code = manifest.get("codeDigests")
    require(isinstance(code, dict) and CODE_INPUTS <= set(code), "consumer_code_provenance_required")
    for name, expected in code.items():
        require(isinstance(name, str) and name.startswith("servers/fastapi/")
                and ".." not in Path(name).parts, "code_path_invalid")
        source = (ROOT / name).resolve()
        require(source.is_relative_to(BACKEND) and digest(read_limited(source)) == expected,
                "capture_consumer_code_digest_mismatch")

    request_wire = referenced(base, manifest["request"])
    request = GeneratePresentationRequest.model_validate(request_wire)
    require(request.strategy == "svg" and request.svg_template_pin is None
            and request.source_mode is None and not request.reference_snapshot_ids
            and not request.files and request.master_pin is None, "standalone_synthetic_svg_request_required")
    outline = PresentationOutlineModel.model_validate(referenced(base, manifest["outline"]))
    snapshot = referenced(base, manifest["styleSnapshot"])
    require(isinstance(snapshot, dict) and requirements_revision(snapshot) == V5, "actual_pinned_v5_snapshot_required")
    require(snapshot.get("digest") == canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True),
        "pinned_snapshot_digest_invalid")
    pin = manifest.get("pinnedInput")
    require(isinstance(pin, dict)
            and pin.get("function") == "services.svg_generation_admission.pin_svg_style"
            and pin.get("snapshotDigest") == snapshot["digest"]
            and pin.get("requestDigest") == digest(canonical(request_wire)), "actual_pin_input_binding_required")
    # The root supplies the actual admitted snapshot. Never add a v5 flag,
    # turn off a policy, regenerate a snapshot from live env, or invent a gate.
    require(not snapshot.get("svg_template") and not snapshot.get("source_authoring_contract"),
            "standalone_snapshot_required")
    request._svg_style_snapshot = deepcopy(snapshot)
    validate_profile_agreement(request, snapshot)
    validate_quality_recovery_agreement(request, snapshot)
    canvas = request_canvas_metadata(request)
    pt_per_px = canvas_point_per_px(canvas)
    physical_canvas = parse_canvas_metadata(canvas)
    width, height = physical_canvas.width_px, physical_canvas.height_px
    require(width > 0 and height > 0, "typed_request_canvas_required")
    readability = validate_svg_readability_policy(snapshot.get("readability_policy"))
    require(readability is not None and parse_canvas_metadata(readability["canvas"]) == physical_canvas,
            "actual_pinned_readability_canvas_required")
    normal_floor = max(generation_font_floor(request.instructions, physical_canvas),
                       svg_readability_font_floor(readability))

    fixture = referenced(base, manifest["fixture"])
    require(isinstance(fixture, dict) and isinstance(fixture.get("examples"), list), "actual_builder_fixture_required")
    require(fixture.get("schema") == "svg-chart-support-graph-examples/v1", "source_fixture_schema_invalid")
    examples = {item["name"]: item for item in fixture["examples"]}
    require(len(examples) == len(fixture["examples"]), "duplicate_source_fixture_names")
    entries = manifest.get("pages")
    require(isinstance(entries, list) and 1 <= len(entries) <= 20
            and len(entries) == len(outline.slides), "page_outline_count_mismatch")
    require(request.n_slides == len(entries), "typed_request_slide_count_mismatch")
    require(request.slides_markdown in (None, [slide.content for slide in outline.slides]), "request_outline_mismatch")
    names, chart_types, pages, expected_pages, page_provenance, roles = set(), set(), [], [], [], []
    for index, entry in enumerate(entries):
        name = entry["fixtureName"]
        require(name in examples and name not in names, "fixture_case_missing_or_duplicate")
        names.add(name)
        example = examples[name]
        requested = example.get("requestedSource")
        require(isinstance(requested, dict) and requested.get("schema") == "synthetic-chart-request/v1"
                and requested.get("sourceDigest") == digest(canonical({
                    "content": requested.get("content"), "payload": requested.get("payload")}))
                and requested.get("provenance", {}).get("authoredBeforeAcceptanceCapture") is True
                and requested.get("provenance", {}).get("generationHttpMeasured") is False,
                "independent_pre_capture_requested_source_required")
        require(outline.slides[index].content == requested["content"], "independent_requested_source_outline_mismatch")
        svg = referenced(base, entry["svg"], as_json=False)
        payload = referenced(base, entry["payload"])
        capture_wire = referenced(base, entry["capture"])
        expected_consumer = referenced(base, entry["consumer"])
        require(isinstance(payload, dict) and payload.get("svg_content") == svg, "payload_svg_mismatch")
        record, current = bound_declarations(payload, svg)
        require(current and record == example["nativeDeclarations"]
                and record["svgDigest"] == native_binding_digest(example["canonicalSvg"]),
                "fixture_declaration_binding_mismatch")
        require(not record.get("dropped") and bool(record.get("objects")), "complete_native_declarations_required")
        static = build_chart_witness_manifest(payload, svg, revision=V5)
        require(isinstance(static, dict) and bool(static.get("charts")), "actual_v5_builder_manifest_required")
        fixture_static = build_chart_witness_manifest(
            {"nativeDeclarations": example["nativeDeclarations"]}, example["canonicalSvg"], revision=V5)
        require(fixture_static == example["manifest"], "source_fixture_builder_provenance_mismatch")
        require(capture_wire.get("type") == "services.svg_renderer.SvgRenderResult", "actual_render_result_required")
        png = base64.b64decode(capture_wire["pngBase64"], validate=True)
        require(png.startswith(b"\x89PNG\r\n\x1a\n"), "actual_capture_png_required")
        capture = SvgRenderResult(png=png, quality=capture_wire["quality"],
                                  wrapper_html=capture_wire["wrapper_html"],
                                  source_digest=capture_wire["source_digest"])
        producer = capture.quality
        quality = payload.get("quality")
        require(isinstance(producer, dict) and PRODUCER_QUALITY_FIELDS <= set(producer)
                and producer.get("passed") is True and producer.get("geometry_passed") is True
                and producer.get("measurement_complete") is True
                and type(producer.get("verified")) is bool and producer.get("failures") == []
                and isinstance(producer.get("browser_metrics"), dict) and bool(producer["browser_metrics"])
                and isinstance(producer.get("contract_version"), str) and bool(producer["contract_version"])
                and isinstance(producer.get("renderer_version"), str) and bool(producer["renderer_version"])
                and isinstance(producer.get("semantic_review"), str) and bool(producer["semantic_review"])
                and isinstance(producer.get("trusted_font_materialization"), dict)
                and producer["trusted_font_materialization"].get("worker_materialized") is True,
                "complete_passing_original_capture_quality_required")
        require(isinstance(quality, dict) and canonical({key: value for key, value in producer.items()
                if key not in DERIVED_QUALITY_FIELDS}) == canonical({key: value for key, value in quality.items()
                if key not in DERIVED_QUALITY_FIELDS}), "bidirectional_capture_page_quality_mismatch")
        # Readability, fonts, passed/failures and capture identity are producer
        # facts. Never substitute supplied page fields for preserved capture data.
        validate_svg_readability_publication(readability, producer)
        require(payload.get("readability_policy") == readability
                and type(producer.get("body_font_floor_px")) in (int, float)
                and producer["body_font_floor_px"] >= normal_floor,
                "capture_must_respect_actual_frozen_font_floor")
        require(capture.source_digest == producer.get("source_digest") == source_digest(svg),
                "capture_canonical_svg_digest_mismatch")
        require(producer.get("png_digest") == digest(png)
                and producer.get("wrapper_digest") == digest(capture.wrapper_html.encode())
                and producer.get("metrics_digest") == digest(canonical(producer["browser_metrics"])),
                "actual_capture_digest_mismatch")
        # Reconsume actual captured pixels/support evidence. A supplied passed
        # boolean is never accepted in place of the existing strict consumer.
        consumer = validate_chart_content_paint(static, producer, capture=capture)
        require(consumer.get("passed") is True and canonical(consumer) == canonical(expected_consumer),
                "strict_v5_consumer_not_positive_or_stale")
        rechecked = {key: deepcopy(value) for key, value in producer.items() if key not in DERIVED_QUALITY_FIELDS}
        apply_requested_native_quality(rechecked, payload, svg, outline.slides[index].content,
                                       pt_per_px=pt_per_px, revision=V5, render_capture=capture, post_render=True)
        require(rechecked.get("passed") is True and not rechecked.get("failures")
                and rechecked.get("requestedNativeObjects", {}).get("requiredCharts", 0) > 0
                and rechecked["requestedNativeObjects"].get("contentRetentionProven") is True
                and rechecked.get("requestedNativeObjects") == quality.get("requestedNativeObjects"),
                "actual_v5_requested_native_quality_mismatch")
        objects, summary = page_native_objects(payload, pt_per_px=pt_per_px, revision=V5)
        require(summary and summary["accepted"] == summary["declared"] and not summary["dropped"],
                "all_declared_native_objects_must_project")
        for item in (objects or {}).get("objects", []):
            if item["kind"] == "chart":
                chart_types.add(item["payload"]["chartType"])
        role = entry.get("role")
        require(role == "content", "synthetic_content_role_required")
        # Final page numbering/furniture must already have been captured. Never
        # modify SVG bytes after validating their PNG and paint evidence.
        validate_svg(svg, width=width, height=height)
        require(finalize_svg(svg, index=index, n=len(entries), width=width, height=height,
                             style_snapshot=snapshot) == svg, "capture_finalized_page_bytes_required")
        expected_pages.append((deepcopy(payload), png))
        computed_payload = deepcopy(payload)
        computed_payload["quality"] = rechecked
        pages.append((computed_payload, png))
        roles.append(role)
        page_provenance.append({"fixtureName": name, "svgDigest": source_digest(svg),
                                "pngDigest": digest(png), "payloadDigest": entry["payload"]["sha256"],
                                "captureDigest": entry["capture"]["sha256"],
                                "consumerDigest": entry["consumer"]["sha256"],
                                "requestedSourceDigest": requested["sourceDigest"],
                                "nativeObjectsDigest": digest(canonical(objects))})
    require(chart_types == CHART_TYPES, "six_supported_native_chart_types_required")
    quality_summary = referenced(base, manifest["qualitySummary"])
    summary = reclassify_pages(pages, summary=quality_summary)
    require(all(actual_png == expected_png and canonical(actual_payload) == canonical(expected_payload)
                for (actual_payload, actual_png), (expected_payload, expected_png)
                in zip(pages, expected_pages, strict=True))
            and canonical(summary) == canonical(quality_summary), "actual_classified_page_summary_required")
    provenance = {"manifestDigest": digest(raw_manifest), "fixtureDigest": manifest["fixture"]["sha256"],
                  "snapshotDigest": snapshot["digest"], "requestDigest": manifest["request"]["sha256"],
                  "outlineDigest": manifest["outline"]["sha256"], "codeDigests": code,
                  "pageCount": len(pages), "chartTypes": sorted(chart_types), "pages": page_provenance}
    return ValidatedReplay(request, outline, snapshot, roles, pages, quality_summary, provenance)


async def normal_principal_call(credentials: dict, callback):
    """Resolve only through the unchanged product middleware, inside explicit DB modes."""
    from sqlalchemy.engine import make_url
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    require(make_url(os.environ.get("DATABASE_URL", "")).get_backend_name() == "postgresql",
            "managed_postgresql_environment_required")
    from api.middlewares import SessionAuthMiddleware
    from api.v1.auth.config import SESSION_COOKIE_NAME
    from services.account_deletion_service import ACCOUNT_DELETION_ACTIVE
    from services.database import require_public_api_database_authority
    from utils.get_env import is_disable_auth_enabled
    from utils.owner_context import get_current_owner_id, get_current_tenant_id, get_request_scope_active

    await require_public_api_database_authority()
    configured_noauth = is_disable_auth_enabled()
    cookie = SimpleCookie()
    if credentials.get("sessionCookie"):
        cookie[SESSION_COOKIE_NAME] = credentials["sessionCookie"]
    require(configured_noauth or bool(cookie), "existing_normal_session_cookie_required")
    request = Request({"type": "http", "method": "POST", "scheme": "http",
                       "path": "/api/v1/ppt/presentation/create", "query_string": b"",
                       "server": ("localhost", 5000), "client": ("127.0.0.1", 0),
                       "headers": [(b"host", b"localhost:5000"),
                                   (b"cookie", cookie.output(header="", sep=";").strip().encode())]})
    require(get_current_owner_id() is None and get_current_tenant_id() is None,
            "clean_principal_resolution_context_required")
    result = None

    async def scoped(normal_request):
        nonlocal result
        principal = getattr(normal_request.state, "auth_principal", None)
        user = getattr(normal_request.state, "current_user", None)
        require(principal is not None and user is not None and user.is_active
                and user.deletion_state == ACCOUNT_DELETION_ACTIVE
                and get_current_owner_id() == principal.user_id
                and get_current_tenant_id() == principal.tenant_id
                and bool(principal.tenant_id) and get_request_scope_active(),
                "genuine_active_owner_principal_context_required")
        result = await callback(normal_request, configured_noauth)
        return JSONResponse({"status": "completed"})

    async def unused_app(_scope, _receive, _send):
        raise ReplayInputError("unexpected_asgi_application_execution")

    response = await SessionAuthMiddleware(unused_app).dispatch(request, scoped)
    require(response.status_code == 200 and result is not None, "normal_product_principal_resolution_failed")
    return result


async def resolve_identity(output: Path, auth_path: Path | None) -> dict:
    """Read the normal principal and write only a new private identity file."""
    credentials = json_bytes(read_limited(auth_path.resolve(), 64 * 1024)) if auth_path else {}
    output = output.resolve()
    require(output.parent.is_dir() and not output.exists(), "new_private_identity_path_required")

    async def save(normal_request, configured_noauth):
        principal = normal_request.state.auth_principal
        record = {"schema": "svg-freeze-runtime-replay-private-identity/v1",
                  "expectedOwnerId": str(principal.user_id), "expectedTenantId": principal.tenant_id,
                  "privateAuditPath": str(output.with_name(output.stem + ".publication.jsonl")),
                  "principal": {"isAdmin": principal.is_admin, "authMethod": principal.method,
                                "authIssuer": principal.issuer, "configuredDisableAuth": configured_noauth},
                  "adversarialRlsCoverage": False}
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
        return {"resolved": True, "identityDigest": digest(canonical(record))}

    return await normal_principal_call(credentials, save)


async def publish(replay: ValidatedReplay, identity_path: Path, auth_path: Path | None) -> dict:
    from sqlalchemy import select, text
    from sqlalchemy.engine import make_url

    require(make_url(os.environ.get("DATABASE_URL", "")).get_backend_name() == "postgresql",
            "managed_postgresql_environment_required")
    from models.sql.presentation import PresentationModel
    from services.account_deletion_service import require_active_account_generation
    from services.database import async_session_maker
    from services.generation_core.drivers.svg.publish import _publish
    from services.workspace_operation_principal import operation_principal

    identity = json_bytes(read_limited(identity_path.resolve(), 64 * 1024))
    require(identity.get("schema") == "svg-freeze-runtime-replay-private-identity/v1"
            and "sessionCookie" not in identity, "separate_resolved_identity_file_required")
    credentials = json_bytes(read_limited(auth_path.resolve(), 64 * 1024)) if auth_path else {}
    owner = uuid.UUID(identity["expectedOwnerId"])
    tenant = identity["expectedTenantId"]
    require(isinstance(tenant, str) and bool(tenant), "explicit_expected_tenant_required")
    private_audit = Path(identity["privateAuditPath"]).resolve()
    require(private_audit.parent.is_dir() and not private_audit.exists(), "new_private_audit_path_required")

    async def scoped_publication(normal_request, configured_noauth):
        principal = getattr(normal_request.state, "auth_principal", None)
        user = getattr(normal_request.state, "current_user", None)
        require(principal is not None and user is not None and user.is_active
                and principal.user_id == owner and principal.tenant_id == tenant,
                "genuine_expected_active_owner_principal_required")
        presentation_id = uuid.uuid4()  # No target/customer ID is accepted.
        # Reuse the worker/service context helper with exactly the middleware's
        # genuine claims. Its admin/trace/request-scope claims remain unchanged.
        with operation_principal(principal.user_id, principal.tenant_id):
            async with async_session_maker() as session:
                authority = (await session.execute(text(
                    "SELECT current_user AS role_name, session_user AS session_role_name, "
                    "r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls, "
                    "s.rolsuper AS session_superuser, s.rolbypassrls AS session_bypass_rls "
                    "FROM pg_roles r JOIN pg_roles s ON s.rolname=session_user WHERE r.rolname=current_user"
                ))).mappings().one()
                require(not any(authority[key] for key in (
                    "superuser", "bypass_rls", "session_superuser", "session_bypass_rls")),
                    "normal_nonbypass_postgresql_role_required")
                generation = await require_active_account_generation(session, user_id=owner)
                require(await session.get(PresentationModel, presentation_id) is None, "new_presentation_id_conflict")
                audit = {"schema": "svg-freeze-runtime-replay-private-publication/v1",
                         "status": "publication_started", "presentationId": str(presentation_id),
                         "ownerId": str(owner), "tenantId": tenant, "accountGeneration": generation,
                         "authMethod": principal.method, "authIssuer": principal.issuer,
                         "isAdmin": principal.is_admin, "configuredDisableAuth": configured_noauth,
                         "databaseRole": dict(authority), "adversarialRlsCoverage": False,
                         "provenance": replay.provenance}
                # Caller chooses an existing private directory with correct ACLs.
                # Retain the new ID before publication so a lost/uncertain local
                # outcome can be reconciled rather than blindly publishing again.
                descriptor = os.open(private_audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(canonical(audit).decode("utf-8") + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    await _publish(replay.request, presentation_id, replay.outline, replay.request.language,
                                   session, None, replay.snapshot, replay.roles, replay.pages,
                                   quality_summary=replay.quality_summary)
                    saved = await session.scalar(select(PresentationModel).where(
                        PresentationModel.id == presentation_id, PresentationModel.owner_id == owner))
                    require(saved is not None and saved.is_svg() and saved.n_slides == len(replay.pages)
                            and saved.revision > 0, "published_owner_scoped_presentation_required")
                    revision = saved.revision
                    completion = {"schema": "svg-freeze-runtime-replay-private-publication-completed/v1",
                                  "status": "published", "presentationId": str(presentation_id),
                                  "revision": revision, "startedRecordDigest": digest(canonical(audit))}
                    # Append only: even a partial completion write leaves the
                    # fsynced original UUID/input record intact for reconciliation.
                    handle.write(canonical(completion).decode("utf-8") + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return {"status": "published", "presentationId": str(presentation_id), "revision": revision,
                        "provenance": replay.provenance}

    return await normal_principal_call(credentials, scoped_publication)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mode", choices=("validate-only", "resolve-identity", "publish"), dest="mode")
    modes.add_argument("--resolve-identity", action="store_const", const="resolve-identity", dest="mode")
    parser.set_defaults(mode="validate-only")
    parser.add_argument("--identity-file", type=Path, help="Private resolved expected-identity file; publish mode only")
    parser.add_argument("--auth-file", type=Path, help="Separate private existing-session cookie file; explicit DB modes only")
    parser.add_argument("--identity-output", type=Path, help="New private identity file for resolve mode")
    args = parser.parse_args()
    try:
        if args.mode == "resolve-identity":
            require(args.manifest is None and args.identity_file is None and args.identity_output is not None,
                    "private_identity_output_required")
            auth_file = args.auth_file or (Path(os.environ["SVG_FREEZE_REPLAY_AUTH_FILE"])
                                           if os.environ.get("SVG_FREEZE_REPLAY_AUTH_FILE") else None)
            result = asyncio.run(resolve_identity(args.identity_output, auth_file))
        else:
            require(args.manifest is not None and args.identity_output is None, "capture_manifest_required")
            require(args.mode != "validate-only" or (args.auth_file is None and args.identity_file is None),
                    "validation_must_not_receive_identity_or_credentials")
            replay = validate_inputs(args.manifest)
        if args.mode == "publish":
            identity_file = args.identity_file or (Path(os.environ["SVG_FREEZE_REPLAY_IDENTITY_FILE"])
                                                   if os.environ.get("SVG_FREEZE_REPLAY_IDENTITY_FILE") else None)
            auth_file = args.auth_file or (Path(os.environ["SVG_FREEZE_REPLAY_AUTH_FILE"])
                                           if os.environ.get("SVG_FREEZE_REPLAY_AUTH_FILE") else None)
            require(identity_file is not None, "private_resolved_identity_file_required")
            result = asyncio.run(publish(replay, identity_file, auth_file))
        elif args.mode == "validate-only":
            result = {"status": "validated-only", "provenance": replay.provenance}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        # Production guards retain their exception semantics. Only the CLI
        # projection is content-free: never dump credentials, SQL, SVG or logs.
        code = str(error) if isinstance(error, ReplayInputError) else "replay_failed_" + type(error).__name__
        print(json.dumps({"status": "failed", "code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
