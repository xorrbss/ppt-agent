"""Fresh operation-owned measurement before extracting a stale-verifier body."""
from copy import copy, deepcopy
from types import SimpleNamespace

from utils.hashing import canonical_json_digest, sha256_hex, is_sha256_hex


async def _published_native_payload(row, owner, snapshot, index, session_factory):
    """Read declarations only from the exact successful publication checkpoint."""
    from sqlalchemy import select
    from models.sql.workspace import WorkspaceOperation
    from models.sql.public_admission import PublicOperationAdmission
    from services.svg_common_header_design_checkpoint import _candidate_key
    from services.workspace_svg_header_retry import header_operation_request_hash
    header = snapshot["common_header"]
    async with session_factory() as session:
        sources = list(await session.scalars(select(WorkspaceOperation).where(
            WorkspaceOperation.presentation_id == owner.presentation_id,
            WorkspaceOperation.owner_id == owner.owner_id,
            WorkspaceOperation.owner_scope == owner.owner_scope,
            WorkspaceOperation.kind == "design.apply", WorkspaceOperation.state == "succeeded",
            WorkspaceOperation.result_revision == owner.expected_revision)))
        if len(sources) != 1:
            raise ValueError("svg_common_header_body_remeasurement_unproven")
        source = sources[0]
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == source.id))
        envelope = source.svg_header_design_checkpoint or {}
        if (source.contract_version != "public-operation/v2"
                or source.execution_profile_version != "public-generation-export/v2"
                or type(source.expected_revision) is not int
                or source.expected_revision + 1 != owner.expected_revision
                or source.completed_at is None or source.attempt_token is not None
                or source.cancel_requested_at is not None or source.fencing_token < 1
                or header_operation_request_hash(source) != source.request_hash
                or envelope.get("identity") != {"owner_id": str(source.id),
                    "owner_type": "operation", "request_digest": source.request_hash}
                or admission is None or admission.owner_id != owner.owner_id
                or admission.tenant_id != owner.request_payload.get("principal_tenant_id")
                or admission.state != "succeeded" or admission.fence != source.fencing_token):
            raise ValueError("svg_common_header_body_remeasurement_unproven")
        page = (row.content["svgReceipt"]["system_furniture"]["generated_common_header"])["page"]
        key = _candidate_key(index, {"candidate_revision": str(header["revision"]),
            "candidate_id": "final-" + header["candidate_id"], "page_id": page["page_id"]})
        payload = deepcopy((envelope.get("candidate_pages") or {}).get(key) or {})
        if (payload.get("svg_content") != row.svg_content or payload.get("terminal") or payload.get("inflight")
                or any(payload.get(name) != row.content["svgReceipt"].get(name)
                    for name in ("input_digest", "quality", "system_furniture", "png_sha256"))):
            raise ValueError("svg_common_header_body_remeasurement_unproven")
        return payload


async def remeasure_header_body(row, *, owner, ctx, request, outline, index,
                                parent, session_factory, source_payload=None):
    from services.svg_common_header import validate_common_header_outline
    from services.svg_common_header_export_evidence import (
        apply_common_header_v2_quality, common_header_verifier_digest,
    )
    from services.svg_contract import source_digest
    from services.svg_export_page_evidence import _common_header_current, _geometry_ok
    from services.svg_export_visual_review import page_measurement_current
    from services.svg_presentation_checkpoint import _read_png, _write_png
    from services.generation_core.drivers.svg.render import attempt_render, RETRY
    from services.svg_page_publication import classify_svg_page_quality
    from services.workspace_svg_header_revision_inputs import raw_body
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    from models.sql.workspace import get_workspace_owner_scope

    def reject():
        raise ValueError("svg_common_header_body_remeasurement_unproven")

    if (not isinstance(parent, dict) or not isinstance(row.content, dict)
            or not isinstance(row.svg_content, str) or not row.svg_content
            or not isinstance(owner.request_payload, dict)
            or not isinstance(getattr(owner, "request_hash", None), str) or not owner.request_hash
            or type(index) is not int or type(ctx.count) is not int or ctx.count < 1
            or type(owner.expected_revision) is not int or owner.expected_revision < 0):
        reject()
    receipt = (row.content or {}).get("svgReceipt") or {}
    if not isinstance(receipt, dict) or not isinstance(receipt.get("quality"), dict):
        reject()
    quality = receipt.get("quality") or {}
    if (not isinstance(receipt.get("system_furniture"), dict)
            or not isinstance(receipt["system_furniture"].get("generated_common_header"), dict)
            or not isinstance(quality.get("common_header"), dict)
            or not isinstance(parent.get("pages"), list)
            or any(not isinstance(page, dict) for page in parent["pages"])
            or not isinstance(parent.get("theme"), dict)
            or not isinstance(parent["theme"].get("svg"), dict)
            or not isinstance(parent["theme"]["svg"].get("styleSnapshot"), dict)
            or not isinstance(parent.get("outline"), dict)):
        reject()
    generated = (receipt.get("system_furniture") or {}).get("generated_common_header") or {}
    header_quality = quality.get("common_header") or {}
    parent_pages = parent.get("pages") or []
    snapshot = (parent.get("theme") or {}).get("svg", {}).get("styleSnapshot") or {}
    frozen_outline = parent.get("outline") or {}
    if (owner.kind != "design.apply" or owner.state != "running" or not owner.attempt_token
            or owner.fencing_token < 1 or owner.owner_scope != get_workspace_owner_scope()
            or owner.owner_id is None or owner.owner_id != get_current_owner_id()
            or not isinstance(get_current_tenant_id(), str) or not get_current_tenant_id()
            or (owner.request_payload or {}).get("principal_tenant_id") != get_current_tenant_id()
            or row.owner_id != owner.owner_id or row.presentation != owner.presentation_id
            or row.index != index or row.source_format != "svg"
            or parent.get("revision") != owner.expected_revision
            or len(parent_pages) != ctx.count or not 0 <= index < len(parent_pages)
            or str(parent_pages[index].get("id")) != str(row.id)
            or parent_pages[index].get("svg_content") != row.svg_content
            or parent_pages[index].get("content") != row.content
            or not isinstance(receipt.get("input_digest"), str) or not receipt["input_digest"]
            or receipt.get("source_digest") != sha256_hex(row.svg_content.encode())
            or any(receipt.get(key) for key in ("terminal", "flag", "inflight"))
            or quality.get("passed") is not True or quality.get("verified") is not True
            or quality.get("classification") != "passed" or quality.get("failures")
            or quality.get("geometry_passed") is not True
            or quality.get("source_digest") != source_digest(row.svg_content)
            or not page_measurement_current(row)
            or generated.get("contract") != snapshot.get("common_header")
            or generated.get("selection") != snapshot.get("common_header_selection")
            or not isinstance(generated.get("contract"), dict)
            or generated.get("contract", {}).get("version") != "svg-common-header/v2"
            or not isinstance(header_quality.get("verifier_digest"), str)
            or not header_quality["verifier_digest"].startswith("sha256:")
            or not is_sha256_hex(header_quality["verifier_digest"][7:])
            or header_quality["verifier_digest"] == common_header_verifier_digest()):
        reject()
    try:
        validate_common_header_outline(snapshot, frozen_outline)
        binding = frozen_outline["slides"][index]["common_header"]
        if (generated.get("page") != binding or generated.get("index") != index
                or generated.get("count") != ctx.count
                or outline.slides[index].page_id != binding["page_id"]):
            reject()
        # Re-evaluate the saved scene/ink with today's verifier without treating
        # it as a new receipt. Every saved header fact except the code digest
        # must still agree; the subsequent real render supplies the new proof.
        checked = deepcopy(quality)
        apply_common_header_v2_quality(checked, row.svg_content,
            generated["contract"], binding, receipt["system_furniture"])
        if (checked.get("passed") is not True or checked.get("failures")
                or {k: v for k, v in checked["common_header"].items() if k != "verifier_digest"}
                != {k: v for k, v in header_quality.items() if k != "verifier_digest"}
                or not _geometry_ok(SimpleNamespace(svg_content=row.svg_content,
                    content={"svgReceipt": {**receipt, "quality": checked}}), checked)):
            reject()
        png = _read_png(receipt)
        if not png or sha256_hex(png) != receipt.get("png_sha256"):
            reject()
    except (KeyError, TypeError, IndexError, AttributeError):
        reject()

    source_ctx = copy(ctx)
    source_ctx.snapshot = deepcopy(snapshot)
    source_ctx.header_pages = [page["common_header"] for page in frozen_outline["slides"]]
    source_ctx.header_operation_public_only = True
    source_ctx.header_render_timeout_seconds = 60
    payload = deepcopy(receipt)
    payload["svg_content"] = row.svg_content
    if (row.content or {}).get("nativeObjects") and not ctx.source_reflow and not source_payload:
        source_payload = await _published_native_payload(row, owner, snapshot, index, session_factory)
    if source_payload:
        from services.generation_core.drivers.svg.native_declarations import native_binding_digest
        if native_binding_digest(source_payload.get("svg_content")) != native_binding_digest(row.svg_content):
            reject()
        for key in ("nativeDeclarations", "nativeRequirements"):
            if key in source_payload:
                payload[key] = deepcopy(source_payload[key])
    if (row.content or {}).get("nativeObjects") and not payload.get("nativeDeclarations") and not ctx.source_reflow:
        reject()
    if (row.content or {}).get("nativeObjects") and not ctx.source_reflow:
        from services.generation_core.drivers.svg.native_publish import page_native_objects, canvas_point_per_px
        from services.authored_canvas import request_canvas_metadata
        objects, summary = page_native_objects(payload,
            pt_per_px=canvas_point_per_px(request_canvas_metadata(request)))
        if objects != row.content["nativeObjects"] or summary != receipt.get("nativeObjects"):
            reject()
    payload.update(svg_content=row.svg_content,
        attempts=[{"kind": "local", "receipt": {"version": "svg-common-header-body-remeasurement/v1"}}],
        cache_only_remeasurement={"source_sha256": sha256_hex(row.svg_content.encode()),
            "png_sha256": receipt["png_sha256"]})

    async def capture(active_owner, page_index, *, payload=None):
        # The original operation/page is immutable. Only the completed witness
        # below is persisted with the new candidate's local body seed.
        if active_owner is not owner or page_index != index:
            reject()

    result = await attempt_render(ctx=source_ctx, request=request,
        presentation_id=owner.presentation_id, index=index,
        content=outline.slides[index].content, payload=payload, svg=row.svg_content,
        ordinal=1, job=owner, checkpoint=capture, async_session_maker=session_factory,
        write_png=_write_png, allow_existing_common_header=True)
    if result is RETRY:
        reject()
    measured = payload.get("quality") or {}
    if (payload.get("svg_content") != row.svg_content
            or payload.get("png_sha256") != receipt["png_sha256"]
            or measured.get("passed") is not True or measured.get("failures")
            or not _common_header_current(SimpleNamespace(svg_content=row.svg_content,
                content={"svgReceipt": payload}), measured)):
        reject()
    measured = classify_svg_page_quality(svg_content=row.svg_content, quality=measured,
        index=index, png_path=payload["png_path"], receipt=payload)
    if measured.get("verified") is not True or measured.get("classification") != "passed":
        reject()
    payload["quality"] = measured
    verified = SimpleNamespace(svg_content=row.svg_content, content={"svgReceipt": payload})
    body, preservation = raw_body(verified)
    witness = {"version": "svg-common-header-body-remeasurement/v1",
        "operation_id": str(owner.id), "request_hash": owner.request_hash,
        "owner_id": str(owner.owner_id), "tenant_id": get_current_tenant_id(),
        "presentation_id": str(owner.presentation_id), "source_revision": owner.expected_revision,
        "source_page_id": str(row.id), "page_index": index,
        "source_receipt_digest": canonical_json_digest(receipt, allow_nan=True),
        "source_input_digest": receipt["input_digest"],
        "source_svg_digest": source_digest(row.svg_content), "source_png_digest": receipt["png_sha256"],
        "body_digest": canonical_json_digest(body),
        "native_declarations_digest": canonical_json_digest(payload.get("nativeDeclarations")),
        "source_native_declarations": deepcopy(payload.get("nativeDeclarations")),
        "source_native_requirements": deepcopy(payload.get("nativeRequirements")),
        "previous_verifier_digest": header_quality["verifier_digest"],
        "current_verifier_digest": common_header_verifier_digest(),
        "remeasured_quality": deepcopy(measured), "remeasured_png_path": payload["png_path"]}
    witness["digest"] = canonical_json_digest(witness, allow_nan=True)
    return body, preservation, witness


def require_body_remeasurement_witness(payload, *, owner, index, parent):
    """Join a new local measurement to its immutable source publication."""
    from services.svg_common_header_export_evidence import common_header_verifier_digest
    from services.svg_contract import source_digest
    from services.svg_export_page_evidence import _geometry_ok
    from services.workspace_svg_header_revision_inputs import raw_body
    if not isinstance(payload, dict) or not isinstance(parent, dict) or type(index) is not int:
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    witness = payload.get("body_reuse_remeasurement")
    if witness is None:
        reuse_parent = payload.get("body_reuse_parent") or {}
        attempts = payload.get("attempts")
        if ((isinstance(reuse_parent, dict) and reuse_parent.get("body_reuse_remeasurement") is not None)
                or any(isinstance(attempt, dict) and isinstance(attempt.get("receipt"), dict)
                    and attempt["receipt"].get("body_remeasurement_digest") is not None
                    for attempt in (attempts if isinstance(attempts, list) else []))):
            raise ValueError("svg_common_header_body_remeasurement_unproven")
        return
    if not isinstance(witness, dict) or not witness:
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    pages = parent.get("pages") or []
    if (not isinstance(pages, list) or not 0 <= index < len(pages)
            or not isinstance(pages[index], dict) or not isinstance(owner.request_payload, dict)):
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    source = pages[index] if 0 <= index < len(pages) else {}
    if (not isinstance(source.get("content"), dict)
            or not isinstance(source["content"].get("svgReceipt"), dict)
            or not isinstance(source.get("id"), str) or not source["id"]
            or not isinstance(source.get("svg_content"), str) or not source["svg_content"]
            or not isinstance(witness.get("remeasured_quality"), dict)
            or not isinstance(payload.get("author_body_svg"), str) or not payload["author_body_svg"]
            or not isinstance(payload.get("attempts"), list) or not payload["attempts"]
            or any(not isinstance(attempt, dict) or not isinstance(attempt.get("receipt"), dict)
                for attempt in payload["attempts"])):
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    receipt = (source.get("content") or {}).get("svgReceipt") or {}
    measured = witness.get("remeasured_quality") or {}
    if (not isinstance(receipt.get("quality"), dict)
            or not isinstance(receipt["quality"].get("common_header"), dict)
            or not isinstance(receipt.get("input_digest"), str) or not receipt["input_digest"]
            or not is_sha256_hex(receipt.get("png_sha256"))
            or not isinstance(measured.get("common_header"), dict)):
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    body = payload.get("author_body_svg")
    original_view = SimpleNamespace(svg_content=source.get("svg_content"),
        content={"svgReceipt": {**receipt, "quality": measured}})
    if (witness.get("version") != "svg-common-header-body-remeasurement/v1"
            or witness.get("digest") != canonical_json_digest(
                {key: value for key, value in witness.items() if key != "digest"}, allow_nan=True)
            or witness.get("operation_id") != str(owner.id)
            or witness.get("request_hash") != owner.request_hash
            or witness.get("owner_id") != str(owner.owner_id)
            or witness.get("tenant_id") != (owner.request_payload or {}).get("principal_tenant_id")
            or witness.get("presentation_id") != str(owner.presentation_id)
            or witness.get("source_revision") != owner.expected_revision
            or parent.get("revision") != owner.expected_revision
            or witness.get("source_page_id") != str(source.get("id"))
            or witness.get("page_index") != index
            or witness.get("source_receipt_digest") != canonical_json_digest(receipt, allow_nan=True)
            or witness.get("source_input_digest") != receipt.get("input_digest")
            or witness.get("source_svg_digest") != source_digest(source.get("svg_content") or "")
            or witness.get("source_png_digest") != receipt.get("png_sha256")
            or witness.get("body_digest") != canonical_json_digest(body)
            or witness.get("native_declarations_digest") != canonical_json_digest(payload.get("nativeDeclarations"))
            or witness.get("source_native_declarations") != payload.get("nativeDeclarations")
            or witness.get("source_native_requirements") != payload.get("nativeRequirements")
            or witness.get("previous_verifier_digest") != (receipt.get("quality", {}).get("common_header") or {}).get("verifier_digest")
            or witness.get("current_verifier_digest") != common_header_verifier_digest()
            or measured.get("source_digest") != witness.get("source_svg_digest")
            or measured.get("png_digest") != "sha256:" + str(witness.get("source_png_digest"))
            or measured.get("passed") is not True or measured.get("verified") is not True
            or measured.get("classification") != "passed" or measured.get("failures")
            or measured.get("geometry_passed") is not True
            or measured.get("measurement_complete") is not True
            or (measured.get("common_header") or {}).get("verifier_digest") != common_header_verifier_digest()
            or not _geometry_ok(original_view, measured)
            or any((attempt.get("receipt") or {}).get("body_remeasurement_digest") != witness.get("digest")
                for attempt in payload["attempts"])
            or any(attempt.get("kind") != "local" for attempt in payload.get("attempts", []))):
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    if raw_body(original_view)[0] != body:
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    if source["content"].get("nativeObjects"):
        from services.authored_canvas import presentation_canvas_metadata
        from services.generation_core.drivers.svg.native_publish import page_native_objects, canvas_point_per_px
        objects, summary = page_native_objects({"svg_content": source["svg_content"],
            "nativeDeclarations": witness.get("source_native_declarations"),
            "nativeRequirements": witness.get("source_native_requirements")},
            pt_per_px=canvas_point_per_px(presentation_canvas_metadata(parent.get("theme"))))
        if objects != source["content"]["nativeObjects"] or summary != receipt.get("nativeObjects"):
            raise ValueError("svg_common_header_body_remeasurement_unproven")
    if (payload.get("quality") or {}).get("passed") is True:
        if raw_body(SimpleNamespace(svg_content=payload.get("svg_content"),
                content={"svgReceipt": payload}))[0] != body:
            raise ValueError("svg_common_header_body_remeasurement_unproven")


async def render_remeasured_body_candidate(ctx, request, outline, owner, index, *, identity,
                                          saved, parent, session_factory):
    """Use the normal renderer once, without entering a provider author loop."""
    from services.svg_common_header_generation import _page_input_digest
    from services.svg_common_header_design_checkpoint import candidate_page_checkpoint
    from services.svg_presentation_checkpoint import _read_png, _write_png
    from services.generation_core.drivers.svg.render import attempt_render, RETRY
    from services.svg_export_page_evidence import _geometry_ok
    from services.svg_export_visual_review import page_measurement_current
    require_body_remeasurement_witness(saved, owner=owner, index=index, parent=parent)
    previous_header = (parent.get("theme") or {}).get("svg", {}).get("styleSnapshot", {}).get("common_header") or {}
    current_header = ctx.snapshot.get("common_header") or {}
    if (saved.get("input_digest") != _page_input_digest(ctx, request, outline, request.language, index)
            or any(current_header.get(key) != previous_header.get(key) for key in ("body_box", "shared_style"))
            or not saved.get("author_body_svg") or saved.get("terminal") or saved.get("inflight")):
        raise ValueError("svg_common_header_body_remeasurement_unproven")
    png = _read_png(saved)
    if (png is not None and saved.get("quality", {}).get("passed") is True
            and page_measurement_current(SimpleNamespace(svg_content=saved.get("svg_content"),
                content={"svgReceipt": saved}))
            and _geometry_ok(SimpleNamespace(svg_content=saved.get("svg_content"),
                content={"svgReceipt": saved}), saved["quality"])):
        return saved, png
    active = copy(ctx)
    active.header_operation_public_only = True
    active.header_render_timeout_seconds = ctx.snapshot["common_header_design_plan"].get("max_render_seconds", 60)
    payload = deepcopy(saved)

    async def checkpoint(active_owner, page_index, *, payload=None):
        return await candidate_page_checkpoint(active_owner, page_index, identity=identity,
            payload=payload, session_maker=session_factory)

    result = await attempt_render(ctx=active, request=request, presentation_id=owner.presentation_id,
        index=index, content=outline.slides[index].content, payload=payload,
        svg=payload["author_body_svg"], ordinal=1, job=owner, checkpoint=checkpoint,
        async_session_maker=session_factory, write_png=_write_png)
    if result is RETRY or payload.get("quality", {}).get("passed") is not True:
        raise ValueError("svg_common_header_body_remeasurement_failed")
    require_body_remeasurement_witness(payload, owner=owner, index=index, parent=parent)
    return result
