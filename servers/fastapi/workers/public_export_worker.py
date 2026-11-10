"""Isolated export worker entrypoint: svg export bundles to verified PPTX/PDF.

The raster PNG bundle and editable-hybrid lane that authored decks used were
removed with the authored strategy (2026-11-10). A bundle that is not an svg
export bundle is refused with ``public_export_bundle_schema_unsupported``.
"""

from __future__ import annotations

from services.isolated_worker_contracts import IsolatedWorkerJob
from workers.public_worker_runtime import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
    WorkerOutput,
    main,
)


async def execute_export(
    job: IsolatedWorkerJob,
    store: LocalContentAddressedStore,
) -> WorkerOutput:
    from workers.public_svg_export_worker import load_svg_export_bundle, execute_svg_export

    svg_bundle = load_svg_export_bundle(job, store)
    if svg_bundle is None:
        raise PublicWorkerExecutionError("public_export_bundle_schema_unsupported")
    try:
        return await execute_svg_export(job, store, bundle=svg_bundle)
    except PublicWorkerExecutionError:
        raise
    except Exception as error:
        raise PublicWorkerExecutionError("svg_export_" + type(error).__name__.lower()) from error


if __name__ == "__main__":
    raise SystemExit(main("export", execute_export))
