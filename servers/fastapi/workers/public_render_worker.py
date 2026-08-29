"""Network-none HTML-to-PNG worker using the existing renderer preflight/CDP path."""

from __future__ import annotations

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    main,
)


async def execute_render(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> ArtifactRef:
    if job.input_artifact.media_type not in {"text/html", "application/xhtml+xml"}:
        raise PublicWorkerExecutionError("public_render_input_media_type_invalid")
    payload = store.read(job.input_artifact)
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PublicWorkerExecutionError("public_render_input_not_utf8") from error

    # Imported only inside the render profile. Admission is repeated here and
    # Chromium request interception remains fail-closed inside slide_capture.
    from utils.renderer_isolation import (
        RendererPolicyError,
        admit_authored_html,
        admit_static_output_html,
    )
    from utils.slide_capture import _render_admitted_html_in_worker

    try:
        try:
            admitted = admit_authored_html(html)
        except RendererPolicyError:
            admitted = admit_static_output_html(html)
        png, _metrics = await _render_admitted_html_in_worker(admitted, timeout=120)
    except Exception as error:
        raise PublicWorkerExecutionError("public_render_failed") from error
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PublicWorkerExecutionError("public_render_output_not_png")
    return store.put(
        png,
        source=job.input_artifact,
        operation_id=job.operation_id,
        kind="slide-png",
        media_type="image/png",
    )


if __name__ == "__main__":
    raise SystemExit(main("render", execute_render))
