"""One predicate decides whether a deck fits, so every gate agrees about it."""

import pytest

from services.authored_local_fit_policy import local_fit_applies
from utils.get_env_programme import is_authored_local_fit_source_reflow_enabled
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, STYLE_REFERENCE


def test_a_style_reference_deck_fits() -> None:
    assert local_fit_applies(reference_mode=STYLE_REFERENCE, source_reflow=False) is True


def test_exact_reconstruction_never_fits() -> None:
    """Reproducing a source slide may not move its geometry, knob or not."""

    assert local_fit_applies(
        reference_mode=EXACT_RECONSTRUCTION, source_reflow=False
    ) is False
    assert local_fit_applies(
        reference_mode=EXACT_RECONSTRUCTION, source_reflow=True
    ) is False


def test_source_reflow_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTHORED_LOCAL_FIT_SOURCE_REFLOW", raising=False)

    assert is_authored_local_fit_source_reflow_enabled() is False
    assert local_fit_applies(reference_mode=STYLE_REFERENCE, source_reflow=True) is False


def test_source_reflow_fits_when_the_knob_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHORED_LOCAL_FIT_SOURCE_REFLOW", "true")

    assert is_authored_local_fit_source_reflow_enabled() is True
    assert local_fit_applies(reference_mode=STYLE_REFERENCE, source_reflow=True) is True
    # The knob cannot reach exact reconstruction.
    assert local_fit_applies(
        reference_mode=EXACT_RECONSTRUCTION, source_reflow=True
    ) is False


@pytest.mark.parametrize("raw", ["1", "TRUE", "yes", "on"])
def test_the_knob_accepts_the_usual_truthy_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AUTHORED_LOCAL_FIT_SOURCE_REFLOW", raw)

    assert local_fit_applies(reference_mode=STYLE_REFERENCE, source_reflow=True) is True


def test_the_knob_is_registered() -> None:
    """An unregistered key would never resolve through the snapshot layer."""

    from constants.settings_registry import get_settings_by_key

    entry = get_settings_by_key()["AUTHORED_LOCAL_FIT_SOURCE_REFLOW"]
    assert entry["type"] == "boolean"
    assert entry["default"] is False
    assert entry["secret"] is False
    assert "fastapi.runtime" in entry["targets"]


def test_both_generation_gates_read_the_same_predicate() -> None:
    """The pipeline fits and generation demands receipts; they cannot disagree.

    A pipeline that fits while generation exempts the deck publishes without
    evidence; the reverse fails the deck with
    ``authored_local_fit_evidence_incomplete``. Both call sites must therefore
    route through ``local_fit_applies`` and nothing else.
    """

    from pathlib import Path

    import services.authored_presentation_generation as generation
    import services.authored_presentation_pipeline as pipeline

    retired = (
        "plan_reference_mode != EXACT_RECONSTRUCTION and not source_reflow",
        "reference_mode != EXACT_RECONSTRUCTION\n        and not source_reflow",
    )
    for module in (pipeline, generation):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "local_fit_applies(" in source, f"{module.__name__} bypasses the predicate"
        for expression in retired:
            assert expression not in source, (
                f"{module.__name__} still decides the fit itself: {expression!r}"
            )
