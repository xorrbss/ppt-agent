"""Network-none HTML-to-PNG worker using the existing renderer preflight/CDP path."""

from __future__ import annotations

import json
from collections.abc import Mapping

from services.authored_canvas import (
    AuthoredCanvasMetadata,
    LANDSCAPE_ASPECT_RATIO,
    canvas_metadata_for_aspect_ratio,
)

from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
)
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    main,
)

LEGACY_RENDER_BUNDLE_SCHEMA = "public-render-bundle/v1"
RENDER_BUNDLE_SCHEMA = "public-render-bundle/v2"
RENDER_RESULT_BUNDLE_SCHEMA = "public-render-result-bundle/v1"
RENDER_BUNDLE_MEDIA_TYPE = "application/vnd.presenton.export-bundle+json"
MAX_RENDER_BATCH_SLIDES = 100


def _read_render_bundle(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> tuple[list[tuple[ArtifactRef, str]], AuthoredCanvasMetadata]:
    if (
        job.input_artifact.kind != "export-bundle"
        or job.input_artifact.media_type != RENDER_BUNDLE_MEDIA_TYPE
    ):
        raise PublicWorkerExecutionError("public_render_input_media_type_invalid")
    try:
        value = json.loads(store.read(job.input_artifact))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicWorkerExecutionError("public_render_bundle_invalid_json") from error
    if not isinstance(value, Mapping):
        raise PublicWorkerExecutionError("public_render_bundle_invalid_shape")
    schema_version = value.get("schemaVersion")
    if schema_version == LEGACY_RENDER_BUNDLE_SCHEMA:
        expected_keys = {"schemaVersion", "slides"}
        canvas = canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)
    elif schema_version == RENDER_BUNDLE_SCHEMA:
        expected_keys = {"schemaVersion", "canvas", "slides"}
        try:
            from services.document_canvas import parse_canvas_metadata
            canvas = parse_canvas_metadata(value.get("canvas"))
        except (TypeError, ValueError) as error:
            raise PublicWorkerExecutionError(
                "public_render_canvas_contract_invalid"
            ) from error
    else:
        raise PublicWorkerExecutionError("public_render_bundle_schema_unsupported")
    if (
        set(value) != expected_keys
        or not isinstance(value.get("slides"), list)
        or not 1 <= len(value["slides"]) <= MAX_RENDER_BATCH_SLIDES
    ):
        raise PublicWorkerExecutionError("public_render_bundle_invalid_shape")
    result: list[tuple[ArtifactRef, str]] = []
    for slide_index, item in enumerate(value["slides"]):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"slideIndex", "artifactRef"}
            or item.get("slideIndex") != slide_index
            or not isinstance(item.get("artifactRef"), Mapping)
        ):
            raise PublicWorkerExecutionError("public_render_bundle_slide_invalid")
        try:
            reference = ArtifactRef.from_wire(item["artifactRef"])
        except IsolatedWorkerContractError as error:
            raise PublicWorkerExecutionError(
                "public_render_bundle_slide_invalid"
            ) from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.created_by_operation_id != job.operation_id
            or reference.kind != "slide-html"
            or reference.media_type not in {"text/html", "application/xhtml+xml"}
        ):
            raise PublicWorkerExecutionError(
                "public_render_bundle_slide_binding_mismatch"
            )
        try:
            html = store.read(reference).decode("utf-8")
        except UnicodeDecodeError as error:
            raise PublicWorkerExecutionError("public_render_input_not_utf8") from error
        result.append((reference, html))
    return result, canvas


def _admit_html(html: str):
    from utils.renderer_isolation import (
        RendererPolicyError,
        admit_authored_html,
        admit_static_output_html,
    )

    try:
        admitted = admit_authored_html(html)
    except RendererPolicyError:
        try:
            admitted = admit_static_output_html(html)
        except RendererPolicyError as static_error:
            raise PublicWorkerExecutionError(
                "public_render_policy_rejected"
            ) from static_error
    try:
        from services.svg_renderer import materialize_trusted_svg_fonts
        return materialize_trusted_svg_fonts(html, admitted)
    except ValueError as error:
        raise PublicWorkerExecutionError("public_render_trusted_font_invalid") from error


async def execute_render(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> ArtifactRef:
    if job.input_artifact.media_type in {"text/html", "application/xhtml+xml"}:
        payload = store.read(job.input_artifact)
        try:
            html = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PublicWorkerExecutionError("public_render_input_not_utf8") from error
        admitted_htmls = [_admit_html(html)]
        bundle_inputs: list[ArtifactRef] | None = None
    else:
        bundle, canvas = _read_render_bundle(job, store)
        admitted_htmls = [_admit_html(html) for _, html in bundle]
        bundle_inputs = [reference for reference, _ in bundle]
    if bundle_inputs is None:
        canvas = canvas_metadata_for_aspect_ratio(LANDSCAPE_ASPECT_RATIO)

    # The worker is already inside the network-none supervised boundary. One
    # Chrome pipe serves the exact operation batch; every slide still uses a
    # fresh target/session and repeats request interception.
    from utils.slide_capture import (
        _render_admitted_html_in_worker,
        _render_admitted_htmls_over_pipe,
    )

    try:
        if bundle_inputs is None:
            rendered = [
                await _render_admitted_html_in_worker(
                    admitted_htmls[0], timeout=120,
                    width_px=canvas.width_px,
                    height_px=canvas.height_px,
                )
            ]
        else:
            rendered = _render_admitted_htmls_over_pipe(
                admitted_htmls,
                timeout=120,
                width_px=canvas.width_px,
                height_px=canvas.height_px,
            )
    except PublicWorkerExecutionError:
        raise
    except Exception as error:
        raise PublicWorkerExecutionError("public_render_failed") from error
    outputs: list[ArtifactRef] = []
    for png, _metrics in rendered:
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) < 24:
            raise PublicWorkerExecutionError("public_render_output_not_png")
        if (
            int.from_bytes(png[16:20], "big") != canvas.width_px
            or int.from_bytes(png[20:24], "big") != canvas.height_px
        ):
            raise PublicWorkerExecutionError("public_render_output_canvas_mismatch")
        outputs.append(
            store.put(
                png,
                source=job.input_artifact,
                operation_id=job.operation_id,
                kind="slide-png",
                media_type="image/png",
            )
        )
    if bundle_inputs is None:
        return outputs[0]
    manifest = json.dumps(
        {
            "schemaVersion": RENDER_RESULT_BUNDLE_SCHEMA,
            "slides": [
                {
                    "slideIndex": index,
                    "artifactRef": reference.to_wire(),
                }
                for index, reference in enumerate(outputs)
            ],
            "runtime": {
                "chromiumProcessLaunchCount": 1,
                "browserReuseCount": max(0, len(outputs) - 1),
                "pageSessionCount": len(outputs),
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return store.put(
        manifest,
        source=job.input_artifact,
        operation_id=job.operation_id,
        kind="export-bundle",
        media_type=RENDER_BUNDLE_MEDIA_TYPE,
    )


if __name__ == "__main__":
    raise SystemExit(main("render", execute_render))
