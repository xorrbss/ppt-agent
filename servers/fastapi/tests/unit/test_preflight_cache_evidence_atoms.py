from types import SimpleNamespace

from services.slidespec_pipeline_performance import (
    EvidenceAtomCache,
    EvidenceAtomCacheKey,
    dom_evidence_digest,
)


def _key(**changes: str) -> EvidenceAtomCacheKey:
    values = {
        "qid": "q-implication-text",
        "metric": "occluded_fraction",
        "sensor_version": "chrome-occlusion/12",
        "source_digest": "sha256:source",
        "render_digest": "sha256:render",
    }
    values.update(changes)
    return EvidenceAtomCacheKey(**values)


def test_atom_key_separates_qid_metric_sensor_source_and_render() -> None:
    baseline = _key().digest

    for field in (
        "qid",
        "metric",
        "sensor_version",
        "source_digest",
        "render_digest",
    ):
        assert _key(**{field: f"changed-{field}"}).digest != baseline


def test_unknown_is_not_negative_cached_by_default() -> None:
    cache: EvidenceAtomCache[dict[str, object]] = EvidenceAtomCache()

    assert not cache.put(
        _key(),
        state="unknown",
        value={"reason": "no_sample_on_owned_paint"},
    )
    assert cache.get(_key()) is None
    assert len(cache) == 0
    assert cache.snapshot_counters()["unknown_skipped"] == 1


def test_unknown_short_ttl_expires_and_is_remeasured() -> None:
    now = [100.0]
    cache: EvidenceAtomCache[str] = EvidenceAtomCache(
        unknown_ttl_seconds=2.0,
        clock=lambda: now[0],
    )

    assert cache.put(_key(), state="unknown", value="transient")
    assert cache.get(_key()).state == "unknown"  # type: ignore[union-attr]
    now[0] = 102.0
    assert cache.get(_key()) is None
    counters = cache.snapshot_counters()
    assert counters["expired"] == 1
    assert counters["misses"] == 1


def test_unrelated_unknown_cannot_erase_confirmed_overlap() -> None:
    cache: EvidenceAtomCache[dict[str, float]] = EvidenceAtomCache(
        unknown_ttl_seconds=1.0
    )
    target = _key()
    footer = _key(qid="q-footer", render_digest="sha256:same-render")

    assert cache.put(
        target,
        state="proven_issue",
        value={"occluded_fraction": 1.0},
    )
    assert cache.put(footer, state="unknown", value={})

    confirmed = cache.get(target)
    incomplete = cache.get(footer)
    assert confirmed is not None and confirmed.state == "proven_issue"
    assert incomplete is not None and incomplete.state == "unknown"


def test_lower_confidence_state_never_overwrites_proven_issue() -> None:
    cache: EvidenceAtomCache[str] = EvidenceAtomCache(unknown_ttl_seconds=1.0)

    assert cache.put(_key(), state="proven_issue", value="confirmed")
    assert not cache.put(_key(), state="unknown", value="sensor-miss")
    assert not cache.put(_key(), state="proven_clean", value="late-clean")
    assert cache.get(_key()).value == "confirmed"  # type: ignore[union-attr]
    assert cache.snapshot_counters()["monotonic_rejections"] == 2


def test_key_rejects_ambiguous_empty_identity() -> None:
    try:
        _key(qid="")
    except ValueError as error:
        assert "requires qid" in str(error)
    else:
        raise AssertionError("empty qid must not share a cache namespace")


def test_proposal_dom_digest_includes_scoped_atom_state_and_identity() -> None:
    def evidence(qid: str, state: str) -> SimpleNamespace:
        return SimpleNamespace(
            defects=(),
            atoms=(
                SimpleNamespace(
                    sensor="chrome",
                    metric="occluded_fraction",
                    state=state,
                    stage="baseline",
                    qid=qid,
                    semantic_id=None,
                    constraint_id=None,
                    dependency_refs=(),
                    reason_code=None,
                    global_scope=False,
                    diagnostics={},
                ),
            ),
        )

    confirmed = dom_evidence_digest(evidence("q-target", "proven_issue"))
    assert confirmed != dom_evidence_digest(evidence("q-target", "unknown"))
    assert confirmed != dom_evidence_digest(evidence("q-footer", "proven_issue"))
