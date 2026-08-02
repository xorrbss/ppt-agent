import json
import re
from pathlib import Path
from types import SimpleNamespace

from scripts import check_template_v2_catalog_candidate as preflight
from templates.v2.catalog import (
    RightsReadinessReport,
    load_template_rights_manifest,
    load_template_v2_catalog,
)


def _default_candidate():
    entry = load_template_v2_catalog().entries[0]
    record = load_template_rights_manifest().records[0]
    return entry, record


def _bound_assets(entry, record):
    candidate = next(
        item for item in entry.source.files if item.role == "candidate_pptx"
    )
    return {
        "native_pptx": {
            "preservation_mode": entry.preservation_mode,
            "source_sha256": candidate.sha256,
            "source_size_bytes": record.source.candidate_pptx_size_bytes,
        }
    }


def test_selected_manifest_source_binding_requires_exact_receipt():
    entry, record = _default_candidate()
    assets = _bound_assets(entry, record)

    assert preflight._candidate_source_is_bound(entry, record, assets)

    assets["native_pptx"]["source_size_bytes"] += 1
    assert not preflight._candidate_source_is_bound(entry, record, assets)


def test_effective_revision_prefers_sidecar_and_rejects_invalid_values():
    template = SimpleNamespace(revision=3)

    assert preflight._effective_revision(template, None) == 3
    assert preflight._effective_revision(
        template, SimpleNamespace(revision=4)
    ) == 4
    assert preflight._effective_revision(template, SimpleNamespace(revision=0)) is None
    assert preflight._effective_revision(template, SimpleNamespace(revision=True)) is None


def test_preflight_success_is_secret_free(monkeypatch, capsys):
    entry, record = _default_candidate()
    published_entry = entry.model_copy(update={"publication_status": "published"})
    secret_value = "operator-key-material-that-must-never-be-printed"
    reviewer_value = "release-reviewer-that-must-never-be-printed"
    monkeypatch.setenv("ENABLE_TEMPLATE_V2", "true")
    monkeypatch.setenv("TEMPLATE_V2_TEMPLATE_ALLOWLIST", entry.id)
    monkeypatch.setenv("TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST", reviewer_value)
    monkeypatch.setenv("TEMPLATE_V2_RIGHTS_ATTESTATION_KEY", secret_value)
    monkeypatch.setattr(
        preflight,
        "_select_candidate",
        lambda **_kwargs: (published_entry, record),
    )
    monkeypatch.setattr(
        preflight,
        "template_v2_database_safety",
        lambda: SimpleNamespace(safe=True, code="template_v2_database_safe"),
    )
    monkeypatch.setattr(
        preflight,
        "evaluate_rights_readiness",
        lambda *_args, **_kwargs: RightsReadinessReport(
            template_id=entry.id,
            ready_for_publication_review=True,
            evidence_set_sha256="0" * 64,
            blockers=(),
        ),
    )

    async def ready_live(_entry, _record):
        return (
            {
                "database_reachable": True,
                "schema_at_head": True,
                "database_row_ready": True,
                "publication_evidence_ready": True,
                "source_binding_ready": True,
                "native_source_ready": True,
                "live_code": "template_v2_catalog_candidate_live_ready",
            },
            True,
        )

    monkeypatch.setattr(preflight, "_run_live_candidate_checks", ready_live)

    assert preflight.main(["--template-id", entry.id]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ready"] is True
    assert payload["rights_approvers_configured"] is True
    assert payload["rights_attestation_key_configured"] is True
    assert secret_value not in output
    assert reviewer_value not in output
    assert "attestation_hmac_sha256" not in output


def test_blocked_candidate_never_runs_live_checks(monkeypatch, capsys):
    entry, record = _default_candidate()
    called = False

    async def unexpected_live(_entry, _record):
        nonlocal called
        called = True
        raise AssertionError("live checks must not run for a blocked candidate")

    monkeypatch.setattr(
        preflight,
        "_select_candidate",
        lambda **_kwargs: (entry, record),
    )
    monkeypatch.setattr(preflight, "_run_live_candidate_checks", unexpected_live)

    assert preflight.main(["--template-id", entry.id]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "template_v2_catalog_candidate_not_published"
    assert payload["ready"] is False
    assert called is False


def test_production_compose_forwards_rights_verifier_inputs_without_values():
    repository_root = Path(__file__).resolve().parents[4]
    compose = (repository_root / "docker-compose.yml").read_text(encoding="utf-8")
    variables = (
        "TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST",
        "TEMPLATE_V2_RIGHTS_ATTESTATION_KEY",
    )

    for service in ("production", "production-gpu"):
        match = re.search(
            rf"(?ms)^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:|\Z)",
            compose,
        )
        assert match is not None
        body = match.group("body")
        for variable in variables:
            assert f"{variable}: ${{{variable}:-}}" in body

    for variable in variables:
        assert compose.count(f"{variable}: ${{{variable}:-}}") == 2
