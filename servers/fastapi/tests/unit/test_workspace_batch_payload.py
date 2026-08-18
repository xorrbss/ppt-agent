from services.workspace_batch_payload import (
    BATCH_KIND,
    BATCH_RESULT_KEYS,
    sanitize_batch_value,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload


def _payload() -> dict:
    return {
        "kind": "authored_batch_remake",
        "status": "partial",
        "presentation_id": "6d0b3a4e-6a9c-4f2a-9d51-2b7c8b0a1f33",
        "requested_count": 3,
        "published_count": 2,
        "failed_count": 1,
        "published_slide_indices": [4, 7],
        "failed_slide_indices": [9],
        "fixed_slide_indices": [4, 7],
        "slide_results": [
            {"slide_index": 4, "status": "published", "preview_task_id": "task-4"},
            {"slide_index": 7, "status": "published", "preview_task_id": "task-7"},
            {
                "slide_index": 9,
                "status": "failed",
                "preview_task_id": "task-9",
                "reason_code": "authored_page_remake_source_changed",
            },
        ],
        "canvas_refresh_required": True,
        "rollback_available": True,
        "result_revision": 42,
    }


def test_batch_result_payload_round_trips_through_the_runner_sanitizer():
    safe = sanitize_workspace_event_payload(_payload(), operation_kind=BATCH_KIND)

    assert safe == _payload()
    assert set(_payload()) - {"presentation_id", "result_revision"} == BATCH_RESULT_KEYS


def test_sanitizer_drops_unknown_keys_and_hostile_slide_results():
    payload = _payload()
    payload["candidate_html"] = "<html>secret</html>"
    payload["slide_results"] = payload["slide_results"] + [
        {"slide_index": -1, "status": "published"},
        {"slide_index": True, "status": "published"},
        {"slide_index": 3, "status": "exploded"},
        "not-a-mapping",
        {
            "slide_index": 11,
            "status": "skipped",
            "preview_task_id": "t" * 500,
            "reason_code": "r" * 500,
            "candidate_html": "<html>secret</html>",
        },
    ]
    payload["published_slide_indices"] = [4, "7", True, -3, 8]

    safe = sanitize_workspace_event_payload(payload, operation_kind=BATCH_KIND)

    assert "candidate_html" not in safe
    assert safe["published_slide_indices"] == [4, 8]
    assert len(safe["slide_results"]) == 4
    assert safe["slide_results"][-1] == {
        "slide_index": 11,
        "status": "skipped",
        "preview_task_id": "t" * 160,
        "reason_code": "r" * 128,
    }


def test_sanitize_batch_value_rejects_scalars_and_unknown_keys():
    assert sanitize_batch_value("published_slide_indices", "0123") == []
    assert sanitize_batch_value("published_slide_indices", 4) == []
    assert sanitize_batch_value("unknown_key", [1, 2, 3]) == []
    assert sanitize_batch_value("slide_results", [{"slide_index": 0}]) == []


def test_batch_keys_are_not_admitted_for_another_operation_kind():
    safe = sanitize_workspace_event_payload(_payload(), operation_kind="slide.regenerate")

    assert "slide_results" not in safe
    assert "published_slide_indices" not in safe
