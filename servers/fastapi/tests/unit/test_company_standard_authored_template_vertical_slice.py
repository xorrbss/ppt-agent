from __future__ import annotations

import uuid

from services.authored_vnext.generation.internal_authored_composition import (
    build_internal_authored_composition,
)
from tests.support.authored_vnext_generation_memory_repository import (
    InMemoryGenerationRepository,
)
from tests.support.authored_vnext_master_memory_repository import (
    InMemoryAuthoredVNextMasterRepository,
)
from utils.authored_styles import resolve_authored_style


def test_masterless_authored_uses_hidden_deterministic_internal_composition(monkeypatch) -> None:
    monkeypatch.delenv("AUTHORED_RETAINED_REFERENCE_ROOT", raising=False)
    owner_id = uuid.uuid4()
    style = resolve_authored_style("tech-blueprint")

    first = build_internal_authored_composition(
        owner_id=owner_id,
        style=style,
        metadata=None,
    )
    replay = build_internal_authored_composition(
        owner_id=owner_id,
        style=style,
        metadata=None,
    )

    assert replay == first
    assert first.master.master_id.startswith("master:internal-authored-")
    assert first.master.digest == first.resolved.pin.master_digest
    assert first.composition.master_digest == first.master.digest
    assert all(
        getattr(first.master.contract.furniture, name).visibility == "hidden"
        for name in ("logo", "header", "footer", "divider", "page_number")
    )

    generation_repository = InMemoryGenerationRepository()
    generation_repository.append_imported_master(first.master)
    generation_repository.append_imported_master(replay.master)
    assert generation_repository.get_master(
        first.master.master_id, first.master.revision
    ) == first.master

    catalog_repository = InMemoryAuthoredVNextMasterRepository()
    catalog_repository.append_master(
        first.master,
        expected_revision=None,
        expected_digest=None,
    )
    assert catalog_repository.get_master(first.master.master_id, 1) == first.master
    assert catalog_repository.list_masters(cursor=None, limit=20).items == []

