from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import uuid

from sqlalchemy import Column, JSON, MetaData, String, Table, create_engine

from scripts.authored_only_census import (
    _read_database,
    build_authored_only_report,
    build_run_metadata,
)
from services.compatibility_migration.census import (
    build_census,
    classify_presentation,
)
from services.compatibility_migration.inventory import build_presentation_inventory


def _presentation(identifier: str, **changes):
    value = {
        "id": identifier,
        "owner_id": "owner-a",
        "generation_mode": "standard",
        "mode": None,
        "version": "v1-standard",
        "theme": None,
        "deck_plan": None,
        "community_design_ids": None,
        "profile_driver_id": None,
        "profile_schema_version": None,
        "file_paths": None,
        "reference_snapshot_ids": None,
    }
    value.update(changes)
    return value


def _slide(presentation: str, index: int = 0, **changes):
    value = {
        "id": f"slide-{presentation}-{index}",
        "owner_id": "owner-a",
        "presentation": presentation,
        "index": index,
        "layout_group": "general",
        "layout": "title",
        "content": {"title": "x"},
        "html_content": None,
        "ui": None,
    }
    value.update(changes)
    return value


def _native_ui():
    return {
        "id": "title",
        "description": "Native editable title slide",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "position": {"x": 1, "y": 1},
                        "size": {"width": 8, "height": 1},
                        "runs": [{"text": "Title", "font": {"size": 18}}],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }


def test_canonical_census_assigns_every_required_classification_once():
    presentations = [
        _presentation("legacy"),
        _presentation("adaptive", mode="adaptive", deck_plan={"slides": []}),
        _presentation("smart", generation_mode="smart"),
        _presentation("template", mode="template", version="v2-standard"),
        _presentation("authored", mode="authored", theme={"mode": "authored"}),
        _presentation("mixed", mode="authored", theme={"mode": "authored"}),
        _presentation("invalid", generation_mode="mystery"),
    ]
    slides = [
        _slide("legacy"),
        _slide("adaptive"),
        _slide(
            "smart",
            layout_group="smart-html",
            layout="smart-html",
            html_content="<section>smart</section>",
        ),
        _slide("template", layout_group="native", ui=_native_ui()),
        _slide(
            "authored",
            layout_group="authored",
            layout="authored:0",
            content={"__authored__": True},
            html_content="<section>authored</section>",
        ),
        _slide("mixed"),
        _slide("invalid"),
    ]

    result = build_census(presentations, slides)

    assert [entry.classification for entry in result] == [
        "legacy",
        "adaptive",
        "smart",
        "template_v2",
        "authored",
        "mixed",
        "invalid",
    ]
    assert len(result) == len(presentations)
    assert all(entry.quarantine for entry in result[-2:])


def test_malformed_slide_is_invalid_and_profile_evidence_conflict_is_mixed():
    authored = _presentation("authored", mode="authored")

    malformed = classify_presentation(
        authored,
        [_slide("authored", html_content=42)],
    )
    mixed = classify_presentation(authored, [_slide("authored")])

    assert malformed.classification == "invalid"
    assert malformed.quarantine_reason == "slide_html_invalid"
    assert mixed.classification == "mixed"
    assert mixed.quarantine_reason == "canonical_profile_slide_evidence_conflict"


def test_inventory_hashes_files_and_reports_owner_isolation_and_drain(tmp_path: Path):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx-source")
    digest = sha256(b"pptx-source").hexdigest()
    presentation = _presentation("deck", file_paths=[str(source)])
    jobs = [{"id": "job-1", "state": "running", "presentation_id": "deck"}]
    checkpoint = {
        "id": "checkpoint-1",
        "job_id": "job-1",
        "render_path": str(source),
        "render_sha256": digest,
    }
    archive = {
        "id": "archive-1",
        "owner_id": "owner-b",
        "storage_key": str(source),
        "sha256": digest,
        "status": "ready",
    }

    inventory = build_presentation_inventory(
        presentation,
        [_slide("deck")],
        generation_jobs=jobs,
        checkpoints=[checkpoint],
        archives=[archive],
    )

    assert inventory.drain_required is True
    assert inventory.active_jobs[0].state == "running"
    assert inventory.checkpoint_count == 1
    assert inventory.archive_checksum_complete is True
    assert inventory.owner_isolation_ok is False
    assert len(inventory.source_digest) == 64
    assert len(inventory.runtime_digest) == 64
    assert len(inventory.artifact_digest) == 64
    assert {item.checksum_status for item in inventory.items} == {"match", "not_recorded"}


def test_fixture_database_report_has_full_coverage_quarantine_and_drain(tmp_path: Path):
    database = tmp_path / "census.db"
    engine = create_engine(f"sqlite:///{database}")
    metadata = MetaData()
    presentations = Table(
        "presentations",
        metadata,
        Column("id", String, primary_key=True),
        Column("owner_id", String),
        Column("generation_mode", String),
        Column("mode", String),
        Column("version", String),
        Column("theme", JSON),
        Column("deck_plan", JSON),
        Column("file_paths", JSON),
        Column("reference_snapshot_ids", JSON),
    )
    slides = Table(
        "slides",
        metadata,
        Column("id", String, primary_key=True),
        Column("owner_id", String),
        Column("presentation", String),
        Column("index", String),
        Column("layout_group", String),
        Column("layout", String),
        Column("content", JSON),
        Column("html_content", String),
        Column("ui", JSON),
    )
    jobs = Table(
        "presentation_generation_jobs",
        metadata,
        Column("id", String, primary_key=True),
        Column("presentation_id", String),
        Column("state", String),
    )
    metadata.create_all(engine)
    deck_id = str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            presentations.insert(),
            [
                {
                    "id": deck_id,
                    "owner_id": "owner-a",
                    "generation_mode": "standard",
                    "version": "v1-standard",
                }
            ],
        )
        connection.execute(
            slides.insert(),
            [
                {
                    "id": "slide-1",
                    "owner_id": "owner-a",
                    "presentation": deck_id,
                    "index": "bad-index",
                    "layout_group": "general",
                    "layout": "title",
                    "content": {},
                }
            ],
        )
        connection.execute(
            jobs.insert(),
            [{"id": "job-1", "presentation_id": deck_id, "state": "queued"}],
        )
    engine.dispose()

    report = build_authored_only_report(_read_database(f"sqlite:///{database}"))
    async_report = build_authored_only_report(
        _read_database(f"sqlite+aiosqlite:///{database}")
    )

    assert report["total_presentations"] == 1
    assert report["coverage_complete"] is True
    assert sum(report["classification_counts"].values()) == 1
    assert report["classification_counts"]["invalid"] == 1
    assert report["quarantine"][0]["reason"] == "slide_index_invalid"
    assert report["active_job_drain"]["required"] is True
    assert report["deletion_readiness"]["status"] == "blocked"
    assert report["deletion_readiness"]["physical_deletion_performed"] is False
    manifest_entry = report["deletion_readiness"]["manifest"][0]
    assert manifest_entry["eligible"] is False
    assert "non_authored_compatibility_required" in manifest_entry["blockers"]
    assert "active_job_drain_incomplete" in manifest_entry["blockers"]
    assert async_report["classification_counts"] == report["classification_counts"]
    assert len(report["evidence_sha256"]) == 64
    json.dumps(report)


def test_run_metadata_records_schema_and_hashes_rollout_salt(monkeypatch):
    monkeypatch.setenv("AUTHORED_ONLY_ROLLOUT_SALT", "private-salt")
    monkeypatch.setenv("AUTHORED_ONLY_CANARY_STAGE", "5")
    metadata = build_run_metadata(
        {"alembic_version": [{"version_num": "abc123"}]},
        run_id="authored-only-20260813-r1",
        release_id="release-2026.08.13",
        build_id="build-42",
        git_commit="b387a34735482d4f6dfbf2b955fbf19fd0d2315e",
    )

    assert metadata["database_schema_revisions"] == ["abc123"]
    assert metadata["flags"]["canary_stage"] == "5"
    assert metadata["flags"]["rollout_salt_sha256"] == sha256(
        b"private-salt"
    ).hexdigest()
    assert "private-salt" not in json.dumps(metadata)
