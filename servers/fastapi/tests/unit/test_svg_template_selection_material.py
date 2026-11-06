"""A template selection stores its heavy material by reference (v2)."""

import asyncio
import json

import pytest

from services.svg_templates import selection_material as material
from services.svg_templates.generation import template_selection
from utils.hashing import canonical_json_digest


def _full():
    package = {"digest": "sha256:" + "1" * 64, "frames": [{"frame_id": "frame-1"}],
               "font_manifest": {"assets": [{"family": "Pretendard", "data_base64": "A" * 200_000}]}}
    adaptations = {"frame-1": {"digest": "sha256:" + "2" * 64, "frame": {"svg": "B" * 100_000}}}
    full = {"template_pin": {"templateId": "t", "revision": 1, "digest": "sha256:" + "a" * 64},
            "mode": "template-redesign", "package": package, "style_projection": {"fonts": {}},
            "adaptations": adaptations, "source_snapshot_id": "s", "version_id": "v",
            "schema_version": material.SELECTION_SCHEMA_V1, "frame_map": {},
            "canvas": {"contractVersion": "document-canvas/v1"}}
    full["digest"] = canonical_json_digest(full)
    return full


@pytest.fixture(autouse=True)
def _clean_cache():
    material.clear_material_cache()
    yield
    material.clear_material_cache()


def test_a_compact_selection_is_small_sealed_and_reads_its_material():
    full = _full()
    compact = material.compact_selection(full)
    assert compact["schema_version"] == material.SELECTION_SCHEMA_V2
    assert "package" not in compact and "adaptations" not in compact
    assert len(json.dumps(compact)) < 2_000 < len(json.dumps(full))
    assert template_selection({"svg_template": compact}) == compact
    assert material.template_package(compact) == full["package"]
    assert material.template_adaptations(compact) == full["adaptations"]
    # The same material always gives the same compact selection (replay comparison).
    assert material.compact_selection(_full()) == compact


def test_a_compact_selection_without_loaded_material_fails_loudly():
    compact = material.compact_selection(_full())
    material.clear_material_cache()
    assert not material.material_loaded(compact)
    with pytest.raises(ValueError, match="svg_template_material_not_loaded"):
        material.template_package(compact)


def test_an_inline_v1_selection_is_read_as_before():
    full = _full()
    assert template_selection({"svg_template": full}) == full
    assert material.template_package(full) is full["package"]
    assert material.template_adaptations(full) is full["adaptations"]


def test_material_is_loaded_through_the_template_loader_and_verified(monkeypatch):
    from services.svg_templates import catalog

    compact = material.compact_selection(_full())
    material.clear_material_cache()
    calls = []

    async def resolve(session, **kwargs):
        calls.append(kwargs)
        return _full()

    monkeypatch.setattr(catalog, "resolve_template_selection", resolve)
    asyncio.run(material.ensure_snapshot_material(None, {"svg_template": compact}))
    assert calls and calls[0]["allow_archived"] is True and calls[0]["mode"] == "template-redesign"
    assert material.template_package(compact)["digest"] == "sha256:" + "1" * 64
    # Loaded once per process.
    asyncio.run(material.ensure_snapshot_material(None, {"svg_template": compact}))
    assert len(calls) == 1

    material.clear_material_cache()

    async def changed(session, **kwargs):
        current = _full()
        current["adaptations"]["frame-1"]["digest"] = "sha256:" + "3" * 64
        return current

    monkeypatch.setattr(catalog, "resolve_template_selection", changed)
    with pytest.raises(ValueError, match="svg_template_material_mismatch"):
        asyncio.run(material.ensure_snapshot_material(None, {"svg_template": compact}))


def test_snapshots_without_a_v2_template_need_no_loading(monkeypatch):
    from services.svg_templates import catalog

    async def forbidden(*args, **kwargs):
        raise AssertionError("no load expected")

    monkeypatch.setattr(catalog, "resolve_template_selection", forbidden)
    asyncio.run(material.ensure_snapshot_material(None, {}))
    asyncio.run(material.ensure_snapshot_material(None, {"svg_template": _full()}))


def test_active_tasks_and_their_children_keep_material_across_lru_eviction():
    async def scenario():
        ready, evicted = asyncio.Event(), asyncio.Event()

        async def generation():
            compact = material.compact_selection(_full())
            ready.set()
            await evicted.wait()
            assert compact["material_key"] not in material._cache
            assert material.template_package(compact)["digest"] == "sha256:" + "1" * 64

            async def child():
                return material.template_adaptations(compact)

            assert await asyncio.create_task(child()) == _full()["adaptations"]

        active = asyncio.create_task(generation())
        await ready.wait()
        for index in range(material._CACHE_LIMIT + 1):
            full = _full()
            full["version_id"] = f"other-{index}"
            material.compact_selection(full)
        assert len(material._cache) == material._CACHE_LIMIT
        evicted.set()
        await active
        # The caller did not acquire the completed task's private reference.
        with pytest.raises(ValueError, match="svg_template_material_not_loaded"):
            material.template_package({**_full(), "schema_version": material.SELECTION_SCHEMA_V2,
                                       "material_key": material.material_key(_full())})

    asyncio.run(scenario())


def test_switching_active_selections_never_returns_another_material():
    first = material.compact_selection(_full())
    second_full = _full()
    second_full["version_id"] = "other"
    second_full["package"]["digest"] = "sha256:" + "9" * 64
    second = material.compact_selection(second_full)
    assert material.template_package(first)["digest"] == "sha256:" + "1" * 64
    assert material.template_package(second)["digest"] == "sha256:" + "9" * 64
