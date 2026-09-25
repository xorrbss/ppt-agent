"""Raster-analysis phase of the product-path driver (generate_raster_deck.py):
reference snapshot -> presentation shell -> raster/analysis -> review/approve.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import time
import uuid

from raster_deck_http import request as _request

ANALYSIS_POLL_SECONDS = 3


def create_reference_snapshot(base: str, asset_id: str) -> dict:
    result = _request(
        base, "POST", "/api/v1/ppt/references/snapshots",
        json_body={"provider_id": "uploaded_document", "source": {"asset_ids": [asset_id]}},
    )
    print(f"[snapshot] snapshot_id={result['snapshot_id']}")
    return result


def create_presentation_shell(base: str, snapshot_id: str, *, orientation: str) -> dict:
    presentation_id = str(uuid.uuid4())
    result = _request(
        base, "POST", "/api/v1/ppt/presentation/create",
        json_body={
            "content": "",
            "presentation_id": presentation_id,
            "reference_snapshot_ids": [snapshot_id],
            "strategy": "svg",
            "page_size": "A4",
            "orientation": orientation,
            "include_title_slide": False,
            "include_table_of_contents": False,
        },
    )
    print(f"[presentation] id={result['id']}")
    return result


def start_raster_analysis(base: str, *, snapshot_id: str, presentation_id: str,
                           orientation: str, analysis_mode: str) -> dict:
    result = _request(
        base, "POST", "/api/v1/ppt/source-content/raster/analysis",
        json_body={
            "reference_snapshot_id": snapshot_id,
            "presentation_id": presentation_id,
            "orientation": orientation,
            "analysis_mode": analysis_mode,
            "style_id": "screenshot-blue",
        },
    )
    print(f"[raster.analysis] operation_id={result['operation_id']} status={result['status']}")
    return result


def wait_for_analysis(base: str, operation_id: str, max_wait_seconds: int = 1800) -> dict:
    deadline = time.time() + max_wait_seconds
    while True:
        if time.time() > deadline:
            raise RuntimeError(f"raster analysis did not reach a terminal state within {max_wait_seconds}s")
        result = _request(base, "GET", f"/api/v1/ppt/source-content/raster/analysis/{operation_id}")
        if result.get("analysis") is not None:
            return result
        # dead_letter/deadline_exceeded are terminal too (missed before: a
        # deck whose analysis dead-lettered polled "analysis is None" forever
        # since only failed/cancelled were treated as terminal).
        if result["status"] in ("failed", "cancelled", "dead_letter", "deadline_exceeded"):
            raise RuntimeError(f"raster analysis {result['status']}: {result.get('error_code')}")
        print(f"[raster.analysis] waiting... status={result['status']} progress={result.get('progress')}")
        time.sleep(ANALYSIS_POLL_SECONDS)


def ensure_approved(base: str, operation: dict) -> dict:
    """Auto-accept any review_required regions/pages as-is and approve.

    This does NOT correct detected roles/text -- it only satisfies the CAS
    review gate so the pipeline can be driven end-to-end unattended. Real
    human review would inspect and fix flagged regions here instead.
    """
    if operation.get("approval_pin"):
        print(f"[raster.review] already approved automatically "
              f"(can_approve={operation.get('can_approve')}, no PATCH needed)")
        operation["_manual_review_used"] = False
        return operation

    print(f"[raster.review] NOT auto-approved (approval_pin absent, can_approve="
          f"{operation.get('can_approve')}, review_blockers={operation.get('review_blockers')}); "
          f"falling back to manual accept-all-as-is review")
    analysis = operation["analysis"]
    region_edits = [
        {"region_id": r["region_id"], "reviewed": True}
        for page in analysis["pages"]
        for r in page["regions"]
    ]
    if region_edits or analysis["unresolved"]:
        operation = _request(
            base, "PATCH", f"/api/v1/ppt/source-content/raster/analysis/{operation['operation_id']}",
            json_body={
                "expected_operation_version": operation["operation_version"],
                "expected_revision": analysis["revision"],
                "analysis_digest": analysis["digest"],
                "region_edits": region_edits,
                "page_edits": [],
                "review_ocr": True,
                "resolve_unresolved": True,
            },
        )
        print(f"[raster.review] marked {len(region_edits)} region(s) reviewed; "
              f"blockers={operation.get('review_blockers')}")

    if operation.get("review_blockers"):
        raise RuntimeError(f"raster review still blocked: {operation['review_blockers']}")

    analysis = operation["analysis"]
    operation = _request(
        base, "POST", f"/api/v1/ppt/source-content/raster/analysis/{operation['operation_id']}/approve",
        json_body={
            "expected_operation_version": operation["operation_version"],
            "expected_revision": analysis["revision"],
            "analysis_digest": analysis["digest"],
        },
    )
    print(f"[raster.review] approved via manual POST /approve, "
          f"approval_pin present={bool(operation.get('approval_pin'))}")
    operation["_manual_review_used"] = True
    return operation
