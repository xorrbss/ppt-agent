"""Lossless wire/storage boundary for upstream Template V2 elements.

The upstream Pydantic models at :data:`UPSTREAM_TEMPLATE_V2_SHA` use
Pydantic's default ``extra="ignore"`` behaviour and contain validators that
coerce some accepted inputs.  Those models are useful at an editor/export
boundary, but serializing through them is not a lossless storage operation.

This module deliberately keeps wire/storage acceptance separate from the
existing strict local models.  A caller must opt in to
``WireTemplateV2Element.validate_strict()`` before editor or export use.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from pydantic import TypeAdapter

from .models.elements import SlideElement
from .models.layouts import SlideLayouts


UPSTREAM_TEMPLATE_V2_SHA = "57b194b234b42c8b28f8a507a30322de200e3e83"

ELEMENT_DISCRIMINATORS = frozenset(
    {
        "text",
        "container",
        "image",
        "text-list",
        "table",
        "vector",
        "chart",
        "infographic",
        "flex",
        "grid",
        "group",
    }
)

_STRICT_ELEMENT_ADAPTER = TypeAdapter(SlideElement)


class TemplateV2WireCodecError(ValueError):
    """Raised when a value cannot cross the Template V2 wire boundary."""


def _assert_json_value(
    value: Any,
    *,
    path: str,
    active: set[int],
) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TemplateV2WireCodecError(
                f"template_v2_wire_non_finite_number:{path}"
            )
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise TemplateV2WireCodecError(
                f"template_v2_wire_recursive_reference:{path}"
            )
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TemplateV2WireCodecError(
                        f"template_v2_wire_non_string_key:{path}"
                    )
                _assert_json_value(
                    item,
                    path=f"{path}.{key}",
                    active=active,
                )
        finally:
            active.remove(identity)
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise TemplateV2WireCodecError(
                f"template_v2_wire_recursive_reference:{path}"
            )
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _assert_json_value(
                    item,
                    path=f"{path}.{index}",
                    active=active,
                )
        finally:
            active.remove(identity)
        return
    raise TemplateV2WireCodecError(
        f"template_v2_wire_non_json_value:{path}:{type(value).__name__}"
    )


def _json_clone(value: Mapping[str, Any]) -> dict[str, Any]:
    _assert_json_value(value, path="$", active=set())
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TemplateV2WireCodecError("template_v2_wire_element_must_be_object")
    return decoded


def _parse_wire_value(
    value: str | bytes | bytearray | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _json_clone(value)
    if not isinstance(value, (str, bytes, bytearray)):
        raise TemplateV2WireCodecError(
            "template_v2_wire_element_must_be_object"
        )
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TemplateV2WireCodecError(
            "template_v2_wire_invalid_json"
        ) from error
    if not isinstance(decoded, dict):
        raise TemplateV2WireCodecError("template_v2_wire_element_must_be_object")
    return _json_clone(decoded)


@dataclass(frozen=True, slots=True)
class WireTemplateV2Element:
    """An isolated, lossless JSON element accepted at wire/storage boundaries."""

    _encoded: str
    discriminator: str

    def to_wire_value(self) -> dict[str, Any]:
        """Return a detached copy preserving every accepted JSON field and type."""

        decoded = json.loads(self._encoded)
        if not isinstance(decoded, dict):
            raise AssertionError("wire element invariant violated")
        return decoded

    def to_storage_value(self) -> dict[str, Any]:
        """Return the lossless JSON object suitable for a JSON storage column."""

        return self.to_wire_value()

    def validate_strict(self) -> SlideElement:
        """Enter the existing strict editor/export validation boundary."""

        return _STRICT_ELEMENT_ADAPTER.validate_python(self.to_wire_value())


def decode_wire_element(
    value: str | bytes | bytearray | Mapping[str, Any],
) -> WireTemplateV2Element:
    """Decode one known upstream element without Pydantic coercion or field loss."""

    decoded = _parse_wire_value(value)
    discriminator = decoded.get("type")
    if not isinstance(discriminator, str):
        raise TemplateV2WireCodecError(
            "template_v2_wire_discriminator_required"
        )
    if discriminator not in ELEMENT_DISCRIMINATORS:
        raise TemplateV2WireCodecError(
            f"template_v2_wire_unknown_discriminator:{discriminator}"
        )
    return WireTemplateV2Element(
        _encoded=json.dumps(
            decoded,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        discriminator=discriminator,
    )


@dataclass(frozen=True, slots=True)
class WireTemplateV2Layouts:
    """A lossless ``{"layouts": [...]}`` API/storage envelope.

    Strict validation intentionally ignores unknown fields only for the
    validation pass. The encoded source remains authoritative, so extensions
    at the envelope, layout, component, element, and nested-value levels are
    not projected away when the value crosses API or database boundaries.
    """

    _encoded: str

    def to_wire_value(self) -> dict[str, Any]:
        """Return a detached copy of the complete layouts envelope."""

        decoded = json.loads(self._encoded)
        if not isinstance(decoded, dict):
            raise AssertionError("wire layouts invariant violated")
        return decoded

    def to_storage_value(self) -> dict[str, Any]:
        """Return the lossless envelope suitable for a JSON storage column."""

        return self.to_wire_value()

    def validate_strict(self) -> SlideLayouts:
        """Validate known fields without treating extensions as projections.

        ``extra="ignore"`` is scoped to this validation call and propagates
        through nested strict Template V2 models. Required fields, semantic
        validators, and the discriminated ``SlideElement`` union still run,
        including rejection of unknown element discriminator values.
        """

        return SlideLayouts.model_validate(
            self.to_wire_value(),
            extra="ignore",
        )


def decode_wire_layouts(
    value: str | bytes | bytearray | Mapping[str, Any],
) -> WireTemplateV2Layouts:
    """Decode a layouts envelope without Pydantic coercion or field loss."""

    decoded = _parse_wire_value(value)
    return WireTemplateV2Layouts(
        _encoded=json.dumps(
            decoded,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def load_storage_layouts(value: Mapping[str, Any]) -> WireTemplateV2Layouts:
    """Load a JSON layouts column through the lossless storage boundary."""

    return decode_wire_layouts(value)


def dump_storage_layouts(layouts: WireTemplateV2Layouts) -> dict[str, Any]:
    """Dump a detached layouts envelope without model serialization."""

    if not isinstance(layouts, WireTemplateV2Layouts):
        raise TypeError("layouts must be a WireTemplateV2Layouts")
    return layouts.to_storage_value()


def load_storage_element(value: Mapping[str, Any]) -> WireTemplateV2Element:
    """Load a JSON storage value through the same lossless acceptance boundary."""

    return decode_wire_element(value)


def dump_storage_element(element: WireTemplateV2Element) -> dict[str, Any]:
    """Dump a detached storage value without invoking a Pydantic model."""

    if not isinstance(element, WireTemplateV2Element):
        raise TypeError("element must be a WireTemplateV2Element")
    return element.to_storage_value()


__all__ = [
    "ELEMENT_DISCRIMINATORS",
    "UPSTREAM_TEMPLATE_V2_SHA",
    "TemplateV2WireCodecError",
    "WireTemplateV2Element",
    "WireTemplateV2Layouts",
    "decode_wire_element",
    "decode_wire_layouts",
    "dump_storage_element",
    "dump_storage_layouts",
    "load_storage_element",
    "load_storage_layouts",
]
