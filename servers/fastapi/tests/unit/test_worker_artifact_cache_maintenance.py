"""Batched worker-cache maintenance evicts exactly what per-put passes evicted."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import os
from pathlib import Path
import random
import uuid

import pytest

from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.artifact_store import ArtifactStoreError, WorkerArtifactTransportCache
from services.worker_artifact_cache_maintenance import (
    CacheEvictionPolicy,
    StagedPut,
    run_cache_eviction_pass,
)


class _Metrics:
    def set_cache_usage(self, *, entries: int, size_bytes: int) -> None:
        pass

    def observe_cache(self, event: str, *, count: int = 1, size_bytes: int = 0) -> None:
        pass


def _write(root: Path, name: str, size: int, mtime: float) -> Path:
    path = root / name[:2] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def _state(root: Path) -> dict[str, tuple[int, float]]:
    return {
        path.name: (path.stat().st_size, path.stat().st_mtime)
        for path in root.rglob("*")
        if path.is_file()
    }


def _scenario(seed: int):
    rng = random.Random(seed)
    policy = CacheEvictionPolicy(ttl_seconds=900, max_cache_bytes=rng.randint(2_000, 12_000))
    start = 1_000_000.0
    existing = [
        (f"pre{index:03d}", rng.randint(100, 2_000), start - rng.uniform(0, 1_800))
        for index in range(rng.randint(0, 25))
    ]
    puts = []
    for step in range(rng.randint(1, 40)):
        if existing and rng.random() < 0.15:
            # A batch re-admits a cached object: same digest, so same size.
            name, size, _ = rng.choice(existing)
        else:
            name, size = f"new{step:03d}", rng.randint(100, 1_500)
        # Staging a large deck can take minutes, so later puts see a later clock.
        puts.append((name, size, start + step * rng.choice((1, 30, 120))))
    return policy, existing, puts


def _run(root: Path, policy, existing, puts, *, batched: bool) -> set[str]:
    for name, size, mtime in existing:
        _write(root, name, size, mtime)
    initial = set(_state(root))
    known: dict[Path, int] = {}
    staged: list[StagedPut] = []
    for name, size, clock in puts:
        current = next((path for path in root.rglob(name) if path.is_file()), None)
        prior_mtime = current.stat().st_mtime if current is not None else None
        if current is None:
            current = _write(root, name, size, clock)
        else:  # a cache hit only refreshes the entry, like ``put_stream``
            os.utime(current, (clock, clock))
        if batched:
            staged.append(StagedPut(path=current, size=size, mtime=clock, clock=clock, prior_mtime=prior_mtime))
        else:
            known = run_cache_eviction_pass(
                root, policy=policy, known_entries=known, metrics=_Metrics(),
                protected=current, clock=lambda clock=clock: clock,
            )
    if batched:
        run_cache_eviction_pass(
            root, policy=policy, known_entries=known, metrics=_Metrics(), staged=staged,
        )
    return initial


@pytest.mark.parametrize("seed", range(80))
def test_one_pass_per_batch_evicts_exactly_what_per_put_passes_evict(tmp_path: Path, seed: int) -> None:
    policy, existing, puts = _scenario(seed)
    per_put_root, batched_root = tmp_path / "per-put", tmp_path / "batched"
    initial = _run(per_put_root, policy, existing, puts, batched=False)
    _run(batched_root, policy, existing, puts, batched=True)

    per_put, batched = _state(per_put_root), _state(batched_root)
    assert set(batched) == set(per_put)
    assert {name: size for name, (size, _) in batched.items()} == {
        name: size for name, (size, _) in per_put.items()
    }
    # Same pre-existing entries evicted (an evicted-then-re-admitted object is
    # present in both results, so it is not an eviction difference).
    assert initial - set(batched) == initial - set(per_put)
    assert sum(size for size, _ in batched.values()) <= max(
        policy.max_cache_bytes, puts[-1][1]
    )


def test_equivalence_scenarios_exercise_both_ttl_and_budget_eviction(tmp_path: Path) -> None:
    ttl_evictions = budget_evictions = 0
    for seed in range(80):
        policy, existing, puts = _scenario(seed)
        root = tmp_path / str(seed)
        _run(root, policy, existing, puts, batched=True)
        survivors = set(_state(root))
        final_clock = puts[-1][2]
        for name, _, mtime in existing:
            if name not in survivors and all(put[0] != name for put in puts):
                if final_clock - mtime > policy.ttl_seconds:
                    ttl_evictions += 1
                else:
                    budget_evictions += 1
    assert ttl_evictions > 0 and budget_evictions > 0


def _reference(payload: bytes) -> ArtifactRef:
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-cache",
        kind="slide-svg",
        sha256=digest,
        size_bytes=len(payload),
        media_type="image/svg+xml",
        storage_key=artifact_storage_key(owner_id=owner_id, tenant_id="tenant-cache", digest=digest),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )


def _counting_cache(root: Path, calls: list) -> WorkerArtifactTransportCache:
    cache = WorkerArtifactTransportCache(root, max_cache_bytes=1 << 20, ttl_seconds=900)
    original = cache._maintain_sync

    def counting(*, protected: Path | None = None, staged=()) -> None:
        calls.append(protected if not staged else [put.path for put in staged])
        original(protected=protected, staged=staged)

    cache._maintain_sync = counting  # type: ignore[method-assign]
    return cache


def test_put_batch_runs_one_maintenance_walk_per_batch(tmp_path: Path) -> None:
    references = [_reference(f"<svg>{index}</svg>".encode()) for index in range(40)]

    async def scenario() -> None:
        unbatched: list = []
        cache = _counting_cache(tmp_path / "unbatched", unbatched)
        for reference in references:
            await cache.put_bytes(reference, f"<svg>{references.index(reference)}</svg>".encode())
        assert len(unbatched) == len(references)  # the previous per-object cost

        batched: list = []
        cache = _counting_cache(tmp_path / "batched", batched)
        async with cache.maintenance_batch():
            for index, reference in enumerate(references):
                await cache.put_bytes(reference, f"<svg>{index}</svg>".encode())
            assert batched == []
        # One walk for the whole batch, replaying all 40 admissions in order.
        assert batched == [[cache._path(reference) for reference in references]]
        assert all([await cache.exists(reference) for reference in references])

        # Outside a batch the per-put pass is unchanged.
        extra = _reference(b"<svg>extra</svg>")
        await cache.put_bytes(extra, b"<svg>extra</svg>")
        assert batched[-1] == cache._path(extra) and len(batched) == 2

    asyncio.run(scenario())


def test_put_batch_still_maintains_when_a_put_fails(tmp_path: Path) -> None:
    good = _reference(b"<svg>ok</svg>")
    bad = _reference(b"<svg>bad</svg>")

    async def scenario() -> None:
        calls: list = []
        cache = _counting_cache(tmp_path / "cache", calls)
        with pytest.raises(ArtifactStoreError):
            async with cache.maintenance_batch():
                await cache.put_bytes(good, b"<svg>ok</svg>")
                await cache.put_bytes(bad, b"<svg>tampered</svg>")
        assert calls == [[cache._path(good)]]

    asyncio.run(scenario())


def test_export_staging_walks_the_cache_once_per_bundle(tmp_path: Path) -> None:
    from services.worker_artifact_cache_maintenance import stage_worker_inputs

    payloads = {}
    references = []
    for index in range(30):
        payload = f"<svg>{index}</svg>".encode()
        reference = _reference(payload)
        payloads[reference.artifact_id] = payload
        references.append(reference)

    class _Authority:
        async def stream_ref_to_object_store(self, reference, destination):
            await destination.put_bytes(reference, payloads[reference.artifact_id])

    async def scenario() -> None:
        calls: list = []
        cache = _counting_cache(tmp_path / "cache", calls)
        await stage_worker_inputs(_Authority(), cache, references)  # one bundle
        await stage_worker_inputs(_Authority(), cache, references[:3])  # a second, all hits
        assert len(calls) == 2
        assert sorted(map(str, calls[0])) == sorted(str(cache._path(r)) for r in references)
        assert all([await cache.exists(reference) for reference in references])

    asyncio.run(scenario())
