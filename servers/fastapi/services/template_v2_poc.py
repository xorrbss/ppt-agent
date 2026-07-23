"""Isolated Template V2 vertical PoC.

This module owns one deliberately small internal template, an adaptive-only
adapter, and an in-memory fixture repository. It does not add routes, database
columns, authored conversion, or production template discovery.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Mapping


TEMPLATE_V2_FORMAT = "v2-standard"
TEMPLATE_V2_FLAG = "ENABLE_TEMPLATE_V2_POC"
TEMPLATE_V2_ALLOWLIST = "TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST"
INTERNAL_TEMPLATE_V2_ID = "internal-v2-title-body"
INTERNAL_TEMPLATE_V2_LAYOUT = "title-body"
TEMPLATE_V2_EXPORT_FORMATS = frozenset({"pptx", "pdf"})


class TemplateV2ContractError(ValueError):
    """A stable, content-free Template V2 validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TemplateV2Policy:
    creation_enabled: bool
    allowed_template_ids: frozenset[str]

    def can_create(self, template_id: str) -> bool:
        return self.creation_enabled and template_id in self.allowed_template_ids

    def can_discover(self, template_id: str) -> bool:
        return self.can_create(template_id)

    def can_read(self, presentation: Mapping[str, Any]) -> bool:
        """Existing marked rows remain readable after the kill switch is used."""

        return presentation.get("version") == TEMPLATE_V2_FORMAT


AdaptiveAdapter = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def get_template_v2_policy(
    environ: Mapping[str, str] | None = None,
) -> TemplateV2Policy:
    values = os.environ if environ is None else environ
    enabled = values.get(TEMPLATE_V2_FLAG, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    allowlist = frozenset(
        item.strip()
        for item in values.get(TEMPLATE_V2_ALLOWLIST, "").split(",")
        if item.strip()
    )
    return TemplateV2Policy(
        creation_enabled=enabled,
        allowed_template_ids=allowlist,
    )


def require_template_v2_creation(
    template_id: str,
    policy: TemplateV2Policy,
) -> None:
    if not policy.creation_enabled:
        raise TemplateV2ContractError("template_v2_creation_disabled")
    if template_id not in policy.allowed_template_ids:
        raise TemplateV2ContractError("template_v2_template_not_allowed")


def adapt_template_v2_to_adaptive(
    source: Mapping[str, Any],
    adapter: AdaptiveAdapter,
) -> dict[str, Any]:
    """Run a concrete adapter behind a defensive adaptive-only boundary.

    The boundary validates the explicit format marker and stable slide IDs,
    supplies a deep copy to the adapter, rejects authored output, and checks
    the minimal shape consumed by the existing adaptive slide persistence
    layer. It never writes the adapted form back over the V2 source.
    """

    _validate_source_envelope(source)
    source_snapshot = deepcopy(source)
    adapted = adapter(deepcopy(source))

    if source != source_snapshot:
        raise TemplateV2ContractError("template_v2_source_mutated")
    if not isinstance(adapted, Mapping):
        raise TemplateV2ContractError("template_v2_invalid_adapter_output")
    if adapted.get("mode") != "adaptive":
        raise TemplateV2ContractError("template_v2_authored_output_forbidden")

    slides = adapted.get("slides")
    if not isinstance(slides, list) or len(slides) != len(source["slides"]):
        raise TemplateV2ContractError("template_v2_invalid_adaptive_slides")

    expected_indices = list(range(len(slides)))
    actual_indices: list[int] = []
    for slide in slides:
        if not isinstance(slide, Mapping):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_slide")
        required = ("layout_group", "layout", "index", "content")
        if any(key not in slide for key in required):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_slide")
        if not isinstance(slide["content"], Mapping):
            raise TemplateV2ContractError("template_v2_invalid_adaptive_content")
        if "html_content" in slide:
            raise TemplateV2ContractError("template_v2_authored_output_forbidden")
        actual_indices.append(slide["index"])

    if actual_indices != expected_indices:
        raise TemplateV2ContractError("template_v2_invalid_slide_order")

    return deepcopy(dict(adapted))


def validate_internal_template_v2(source: Mapping[str, Any]) -> None:
    """Validate the exact schema of the single phase-one internal template."""

    _validate_source_envelope(source)
    _require_exact_keys(
        source,
        {"version", "presentation_id", "template_id", "title", "slides"},
        "template_v2_invalid_presentation_fields",
    )
    if source["template_id"] != INTERNAL_TEMPLATE_V2_ID:
        raise TemplateV2ContractError("template_v2_unknown_internal_template")
    if not _is_stable_id(source["presentation_id"]):
        raise TemplateV2ContractError("template_v2_missing_presentation_id")
    if not isinstance(source["title"], str):
        raise TemplateV2ContractError("template_v2_invalid_title")

    node_ids: list[str] = []
    for slide in source["slides"]:
        _require_exact_keys(
            slide,
            {"id", "layout", "nodes"},
            "template_v2_invalid_slide_fields",
        )
        if slide["layout"] != INTERNAL_TEMPLATE_V2_LAYOUT:
            raise TemplateV2ContractError("template_v2_unknown_layout")
        nodes = slide["nodes"]
        if not isinstance(nodes, list):
            raise TemplateV2ContractError("template_v2_invalid_nodes")

        slots: set[str] = set()
        for node in nodes:
            if not isinstance(node, Mapping):
                raise TemplateV2ContractError("template_v2_invalid_node")
            _require_exact_keys(
                node,
                {"id", "type", "slot", "text"},
                "template_v2_invalid_node_fields",
            )
            if node["type"] != "text":
                raise TemplateV2ContractError("template_v2_unknown_node_type")
            if node["slot"] not in {"title", "body"}:
                raise TemplateV2ContractError("template_v2_unknown_text_slot")
            if node["slot"] in slots:
                raise TemplateV2ContractError("template_v2_duplicate_text_slot")
            if not isinstance(node["text"], str):
                raise TemplateV2ContractError("template_v2_invalid_text")
            if not _is_stable_id(node["id"]):
                raise TemplateV2ContractError("template_v2_missing_node_id")
            slots.add(node["slot"])
            node_ids.append(node["id"])

        if slots != {"title", "body"}:
            raise TemplateV2ContractError("template_v2_missing_text_slot")

    if len(node_ids) != len(set(node_ids)):
        raise TemplateV2ContractError("template_v2_duplicate_node_id")


def adapt_internal_template_v2(source: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the internal V2 title/body fixture into adaptive slide records."""

    validate_internal_template_v2(source)

    def adapter(value: Mapping[str, Any]) -> Mapping[str, Any]:
        slides = []
        for index, slide in enumerate(value["slides"]):
            nodes_by_slot = {node["slot"]: node for node in slide["nodes"]}
            slides.append(
                {
                    "layout_group": INTERNAL_TEMPLATE_V2_ID,
                    "layout": INTERNAL_TEMPLATE_V2_LAYOUT,
                    "index": index,
                    "content": {
                        "title": nodes_by_slot["title"]["text"],
                        "body": nodes_by_slot["body"]["text"],
                    },
                    "properties": {
                        "template_v2": {
                            "source_slide_id": slide["id"],
                            "source_node_ids": {
                                slot: nodes_by_slot[slot]["id"]
                                for slot in ("title", "body")
                            },
                        }
                    },
                }
            )
        return {
            "mode": "adaptive",
            "source_version": TEMPLATE_V2_FORMAT,
            "source_presentation_id": value["presentation_id"],
            "template_id": INTERNAL_TEMPLATE_V2_ID,
            "title": value["title"],
            "slides": slides,
        }

    return adapt_template_v2_to_adaptive(source, adapter)


def build_template_v2_export_contract(
    source: Mapping[str, Any],
    export_as: str,
) -> dict[str, Any]:
    """Build a JSON-safe adaptive contract accepted by PPTX/PDF call sites."""

    if export_as not in TEMPLATE_V2_EXPORT_FORMATS:
        raise TemplateV2ContractError("template_v2_unsupported_export_format")
    presentation = adapt_internal_template_v2(source)
    _validate_export_ready_adaptive(presentation)
    contract = {"export_as": export_as, "presentation": presentation}
    try:
        json.dumps(contract, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise TemplateV2ContractError("template_v2_export_not_json_safe") from error
    return deepcopy(contract)


class FixtureTemplateV2Repository:
    """Data-preserving in-memory repository for vertical contract tests only."""

    def __init__(self, policy: TemplateV2Policy):
        self.policy = policy
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._cursor: dict[str, int] = {}

    def set_policy(self, policy: TemplateV2Policy) -> None:
        self.policy = policy

    def create(self, source: Mapping[str, Any]) -> dict[str, Any]:
        validate_internal_template_v2(source)
        require_template_v2_creation(source["template_id"], self.policy)
        presentation_id = source["presentation_id"]
        if presentation_id in self._history:
            raise TemplateV2ContractError("template_v2_presentation_exists")
        stored = deepcopy(dict(source))
        self._history[presentation_id] = [stored]
        self._cursor[presentation_id] = 0
        return deepcopy(stored)

    def save(self, source: Mapping[str, Any]) -> dict[str, Any]:
        validate_internal_template_v2(source)
        presentation_id = source["presentation_id"]
        self._require_existing(presentation_id)
        cursor = self._cursor[presentation_id]
        history = self._history[presentation_id][: cursor + 1]
        stored = deepcopy(dict(source))
        history.append(stored)
        self._history[presentation_id] = history
        self._cursor[presentation_id] = len(history) - 1
        return deepcopy(stored)

    def reopen(self, presentation_id: str) -> dict[str, Any]:
        self._require_existing(presentation_id)
        return deepcopy(self._history[presentation_id][self._cursor[presentation_id]])

    def duplicate(
        self,
        presentation_id: str,
        duplicate_presentation_id: str,
    ) -> dict[str, Any]:
        self._require_existing(presentation_id)
        if not _is_stable_id(duplicate_presentation_id):
            raise TemplateV2ContractError("template_v2_missing_presentation_id")

        duplicate = self.reopen(presentation_id)
        duplicate["presentation_id"] = duplicate_presentation_id
        for slide in duplicate["slides"]:
            slide["id"] = _duplicate_id(duplicate_presentation_id, slide["id"])
            for node in slide["nodes"]:
                node["id"] = _duplicate_id(duplicate_presentation_id, node["id"])
        return self.create(duplicate)

    def undo(self, presentation_id: str) -> dict[str, Any]:
        self._require_existing(presentation_id)
        if self._cursor[presentation_id] == 0:
            raise TemplateV2ContractError("template_v2_nothing_to_undo")
        self._cursor[presentation_id] -= 1
        return self.reopen(presentation_id)

    def redo(self, presentation_id: str) -> dict[str, Any]:
        self._require_existing(presentation_id)
        next_cursor = self._cursor[presentation_id] + 1
        if next_cursor >= len(self._history[presentation_id]):
            raise TemplateV2ContractError("template_v2_nothing_to_redo")
        self._cursor[presentation_id] = next_cursor
        return self.reopen(presentation_id)

    def _require_existing(self, presentation_id: str) -> None:
        if presentation_id not in self._history:
            raise TemplateV2ContractError("template_v2_presentation_not_found")


def _validate_source_envelope(source: Mapping[str, Any]) -> None:
    if not isinstance(source, Mapping):
        raise TemplateV2ContractError("template_v2_invalid_presentation")
    if source.get("version") != TEMPLATE_V2_FORMAT:
        raise TemplateV2ContractError("template_v2_unsupported_version")

    template_id = source.get("template_id")
    if not isinstance(template_id, str) or not template_id.strip():
        raise TemplateV2ContractError("template_v2_missing_template_id")

    slides = source.get("slides")
    if not isinstance(slides, list) or not slides:
        raise TemplateV2ContractError("template_v2_missing_slides")

    ids: list[str] = []
    for slide in slides:
        if not isinstance(slide, Mapping):
            raise TemplateV2ContractError("template_v2_invalid_slide")
        slide_id = slide.get("id")
        if not isinstance(slide_id, str) or not slide_id.strip():
            raise TemplateV2ContractError("template_v2_missing_slide_id")
        ids.append(slide_id)

    if len(ids) != len(set(ids)):
        raise TemplateV2ContractError("template_v2_duplicate_slide_id")


def _validate_export_ready_adaptive(presentation: Mapping[str, Any]) -> None:
    if presentation.get("mode") != "adaptive":
        raise TemplateV2ContractError("template_v2_authored_output_forbidden")
    if "html_content" in presentation:
        raise TemplateV2ContractError("template_v2_authored_output_forbidden")
    for slide in presentation["slides"]:
        if "html_content" in slide:
            raise TemplateV2ContractError("template_v2_authored_output_forbidden")
        content = slide["content"]
        if set(content) != {"title", "body"}:
            raise TemplateV2ContractError("template_v2_invalid_export_content")
        if not all(isinstance(content[key], str) for key in ("title", "body")):
            raise TemplateV2ContractError("template_v2_invalid_export_content")


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    error_code: str,
) -> None:
    if set(value) != expected:
        raise TemplateV2ContractError(error_code)


def _is_stable_id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _duplicate_id(presentation_id: str, source_id: str) -> str:
    return f"{presentation_id}:{source_id}"
