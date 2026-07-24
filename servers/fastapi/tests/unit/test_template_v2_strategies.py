import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from templates.v2.constants import (
    LEGACY_PRESENTATION_VERSION,
    TEMPLATE_V2_VERSION,
)
from templates.v2.policy import (
    StructuredTemplatePolicyError,
    get_structured_template_policy,
)
from templates.v2.strategies import (
    AUTHORED_STRATEGIES,
    TEMPLATE_V2_STRATEGIES,
    EditorCapability,
    ExportStrategy,
    GenerationStrategy,
    PresentationAdapterRegistry,
    StrategyResolutionError,
    resolve_presentation_adapters,
    resolve_presentation_strategies,
)

STRATEGY_PARITY_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "template_v2"
    / "strategy-parity.json"
)


def _strategy_parity_cases():
    contract = json.loads(STRATEGY_PARITY_FIXTURE.read_text(encoding="utf-8"))
    assert contract["contract"] == "persisted-presentation-strategy-v1"
    return contract["cases"]


def test_template_v2_flag_is_default_off_and_existing_v2_remains_readable():
    policy = get_structured_template_policy({})

    assert policy.creation_enabled is False
    assert policy.can_discover("existing") is False
    assert policy.can_read_existing(TEMPLATE_V2_VERSION) is True
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_creation_disabled",
    ):
        policy.require_write_enabled("existing")


def test_template_v2_allowlist_is_a_creation_kill_switch():
    policy = get_structured_template_policy(
        {
            "ENABLE_TEMPLATE_V2": "true",
            "TEMPLATE_V2_TEMPLATE_ALLOWLIST": "allowed-a, allowed-b",
        }
    )

    assert policy.can_discover("allowed-a") is True
    assert policy.can_discover("blocked") is False
    policy.require_write_enabled("allowed-b")
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_template_id_required",
    ):
        policy.require_write_enabled()
    with pytest.raises(
        StructuredTemplatePolicyError,
        match="template_v2_template_not_allowed",
    ):
        policy.require_write_enabled("blocked")


def test_strategy_resolver_selects_native_template_v2_from_identity_and_payload():
    presentation = SimpleNamespace(
        version=TEMPLATE_V2_VERSION,
        mode="template",
        layout={"name": "legacy executor remains delegated"},
    )

    assert resolve_presentation_strategies(
        presentation,
        [{"ui": {"id": "native"}, "html_content": None}],
    ) == TEMPLATE_V2_STRATEGIES


def test_template_v2_requires_exact_template_mode_identity():
    presentation = SimpleNamespace(
        version=TEMPLATE_V2_VERSION,
        mode=None,
        layout=None,
        theme=None,
        is_authored=lambda: True,
    )

    with pytest.raises(
        StrategyResolutionError,
        match="template_v2_identity_mismatch",
    ):
        resolve_presentation_strategies(
            presentation,
            [{"ui": {"id": "native"}, "html_content": None}],
        )


def test_strategy_resolver_selects_authored_hybrid_without_replacing_it():
    presentation = SimpleNamespace(
        version=LEGACY_PRESENTATION_VERSION,
        mode="authored",
        layout=None,
    )

    assert resolve_presentation_strategies(
        presentation,
        [{"ui": None, "html_content": "<section>Editable</section>"}],
    ) == AUTHORED_STRATEGIES


def test_default_off_preserves_authored_html_and_authored_hybrid_contract():
    policy = get_structured_template_policy({})
    presentation = {
        "version": LEGACY_PRESENTATION_VERSION,
        "mode": "authored",
        "layout": None,
    }
    slides = [{"ui": None, "html_content": "<section>Editable</section>"}]
    before = deepcopy((presentation, slides))

    strategies = resolve_presentation_strategies(presentation, slides)

    assert policy.creation_enabled is False
    assert strategies.generation is GenerationStrategy.AUTHORED_HTML
    assert strategies.editor is EditorCapability.AUTHORED_HTML
    assert strategies.export is ExportStrategy.AUTHORED_HYBRID
    assert (presentation, slides) == before


@pytest.mark.parametrize("mode", ["template", "adaptive"])
def test_default_off_leaves_legacy_template_and_adaptive_on_existing_pipeline(mode):
    policy = get_structured_template_policy({})
    presentation = {
        "version": LEGACY_PRESENTATION_VERSION,
        "mode": mode,
        "layout": {"name": "existing-layout"},
    }
    slides = [{"ui": None, "html_content": None}]
    before = deepcopy((presentation, slides))

    with pytest.raises(
        StrategyResolutionError,
        match="legacy_strategy_managed_by_existing_pipeline",
    ):
        resolve_presentation_strategies(presentation, slides)

    assert policy.creation_enabled is False
    assert (presentation, slides) == before


def test_adapter_registry_delegates_to_existing_executors_after_resolution():
    template_generation = object()
    authored_generation = object()
    template_editor = object()
    authored_editor = object()
    template_export = object()
    authored_export = object()
    registry = PresentationAdapterRegistry(
        generation={
            GenerationStrategy.TEMPLATE_V2: template_generation,
            GenerationStrategy.AUTHORED_HTML: authored_generation,
        },
        editor={
            EditorCapability.TEMPLATE_V2: template_editor,
            EditorCapability.AUTHORED_HTML: authored_editor,
        },
        export={
            ExportStrategy.TEMPLATE_V2_GENERAL: template_export,
            ExportStrategy.AUTHORED_HYBRID: authored_export,
        },
    )

    selected = resolve_presentation_adapters(
        {"version": TEMPLATE_V2_VERSION, "mode": "template"},
        [{"ui": {"id": "native"}, "html_content": None}],
        registry,
    )

    assert selected.generation is template_generation
    assert selected.editor is template_editor
    assert selected.export is template_export


def test_adapter_registry_requires_every_strategy_binding():
    with pytest.raises(
        StrategyResolutionError,
        match="generation_adapter_registry_incomplete",
    ):
        PresentationAdapterRegistry(
            generation={
                GenerationStrategy.TEMPLATE_V2: object(),
            },
            editor={
                EditorCapability.TEMPLATE_V2: object(),
                EditorCapability.AUTHORED_HTML: object(),
            },
            export={
                ExportStrategy.TEMPLATE_V2_GENERAL: object(),
                ExportStrategy.AUTHORED_HYBRID: object(),
            },
        )


@pytest.mark.parametrize(
    "case",
    _strategy_parity_cases(),
    ids=lambda case: case["name"],
)
def test_python_and_typescript_share_persisted_strategy_semantics(case):
    presentation = case["presentation"]
    slides = presentation["slides"]
    identity = {
        key: value
        for key, value in presentation.items()
        if key != "slides"
    }
    expected = case["expected"]

    if "error" in expected:
        with pytest.raises(StrategyResolutionError) as error:
            resolve_presentation_strategies(identity, slides)
        assert error.value.code == expected["error"]
        return

    strategies = resolve_presentation_strategies(identity, slides)
    assert strategies.export.value == expected["strategy"]


@pytest.mark.parametrize(
    ("presentation", "slides", "code"),
    [
        (
            {"version": TEMPLATE_V2_VERSION, "mode": "template"},
            [{"ui": {"id": "native"}, "html_content": "<section>mixed</section>"}],
            "mixed_slide_payload_forbidden",
        ),
        (
            {"version": TEMPLATE_V2_VERSION, "mode": "template"},
            [{"ui": None, "html_content": None}],
            "template_v2_ui_payload_required",
        ),
        (
            {"version": LEGACY_PRESENTATION_VERSION, "mode": "authored"},
            [{"ui": {"id": "native"}, "html_content": None}],
            "authored_identity_payload_mismatch",
        ),
        (
            {
                "version": LEGACY_PRESENTATION_VERSION,
                "mode": "template",
                "layout": {"name": "existing"},
            },
            [{"ui": None, "html_content": None}],
            "legacy_strategy_managed_by_existing_pipeline",
        ),
        (
            {
                "version": LEGACY_PRESENTATION_VERSION,
                "mode": "template",
                "layout": {"name": "existing"},
            },
            [{"ui": {"id": "native"}, "html_content": None}],
            "legacy_payload_identity_mismatch",
        ),
        (
            {"version": "v9-unknown", "mode": "template", "layout": {}},
            [{"ui": None, "html_content": None}],
            "unsupported_presentation_identity",
        ),
        (
            {"version": TEMPLATE_V2_VERSION, "mode": "adaptive"},
            [{"ui": {"id": "native"}, "html_content": None}],
            "template_v2_identity_mismatch",
        ),
        (
            {"version": TEMPLATE_V2_VERSION, "mode": "bogus"},
            [{"ui": {"id": "native"}, "html_content": None}],
            "unsupported_presentation_identity",
        ),
        (
            {
                "version": TEMPLATE_V2_VERSION,
                "mode": "template",
                "theme": {"mode": "authored"},
            },
            [{"ui": {"id": "native"}, "html_content": None}],
            "presentation_identity_conflict",
        ),
        (
            {
                "version": TEMPLATE_V2_VERSION,
                "mode": "template",
                "theme": {"mode": "adaptive"},
            },
            [{"ui": {"id": "native"}, "html_content": None}],
            "presentation_identity_conflict",
        ),
        (
            {
                "version": TEMPLATE_V2_VERSION,
                "mode": "template",
                "theme": {"mode": "bogus"},
            },
            [{"ui": {"id": "native"}, "html_content": None}],
            "unsupported_presentation_identity",
        ),
        (
            {
                "version": LEGACY_PRESENTATION_VERSION,
                "mode": "authored",
                "theme": {"mode": "template"},
            },
            [{"ui": None, "html_content": "<section>authored</section>"}],
            "presentation_identity_conflict",
        ),
        (
            {
                "version": LEGACY_PRESENTATION_VERSION,
                "mode": None,
                "theme": {"mode": "bogus"},
                "layout": None,
            },
            [{"ui": None, "html_content": "<section>authored</section>"}],
            "unsupported_presentation_identity",
        ),
        (
            {"version": LEGACY_PRESENTATION_VERSION, "mode": "authored"},
            [],
            "presentation_slides_required",
        ),
    ],
)
def test_strategy_resolver_rejects_inconsistent_or_unmanaged_states(
    presentation, slides, code
):
    with pytest.raises(StrategyResolutionError, match=code):
        resolve_presentation_strategies(presentation, slides)
