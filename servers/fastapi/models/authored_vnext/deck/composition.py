"""Immutable authored master/style composition contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from ..master.base import Digest, StrictCamelModel
from ..master.contract import Rect


HexColor = str


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class BodyVisualSnapshot(StrictCamelModel):
    """Geometry-free body tokens materialized once for preview and export."""

    schema_version: Literal["authored-body-visual/v1"] = "authored-body-visual/v1"
    accent: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    panel_fill: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_text_color: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    warning: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    panel_border_color: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    chart_palette: list[HexColor] = Field(min_length=1, max_length=12)
    table_header_fill: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    process_accent: HexColor = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_font: str = Field(min_length=1, max_length=200)
    body_font: str = Field(min_length=1, max_length=200)
    density: Literal["compact", "balanced", "airy"] = "balanced"
    panel_enabled: bool = True
    image_direction: str | None = Field(default=None, max_length=1000)
    corner_radius_pt: float = Field(default=8.0, ge=0, le=72)

    @model_validator(mode="after")
    def _chart_colors_are_hex(self) -> "BodyVisualSnapshot":
        if any(
            len(color) != 7
            or not color.startswith("#")
            or any(character not in "0123456789abcdefABCDEF" for character in color[1:])
            for color in self.chart_palette
        ):
            raise ValueError("chartPalette entries must be #RRGGBB colors")
        return self


class AuthoredStylePin(StrictCamelModel):
    """Immutable authored-style input projected from a composition pin.

    This is a typed view over the v1 wire contract rather than a second source
    of truth.  ``AuthoredCompositionPin.digest`` remains the durable identity.
    """

    schema_version: Literal["authored-style-pin/v1"] = "authored-style-pin/v1"
    style_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    style_digest: Digest | None = None
    body_visual: BodyVisualSnapshot
    authored_template_digest: Digest | None = None
    digest: Digest

    @model_validator(mode="after")
    def _style_pin_is_sealed(self) -> "AuthoredStylePin":
        if (self.style_id is None) != (self.style_digest is None):
            raise ValueError("styleId and styleDigest must be present together")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        if self.digest != _canonical_digest(payload):
            raise ValueError("authored style pin digest mismatch")
        return self


class CompanyStandardPin(StrictCamelModel):
    """Immutable company master/brand/furniture input of a composition."""

    schema_version: Literal["company-standard-pin/v1"] = "company-standard-pin/v1"
    master_digest: Digest
    consistency_policy_digest: Digest | None = None
    effective_contract_digest: Digest
    digest: Digest

    @model_validator(mode="after")
    def _company_standard_pin_is_sealed(self) -> "CompanyStandardPin":
        payload = self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        if self.digest != _canonical_digest(payload):
            raise ValueError("company standard pin digest mismatch")
        return self


class ReservedZoneSnapshot(StrictCamelModel):
    """One company-owned area that authored content must never enter."""

    id: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[a-z][a-z0-9._:-]*$",
    )
    kind: Literal[
        "logo",
        "pageNumber",
        "header",
        "footer",
        "divider",
        "fixedText",
        "backgroundDecoration",
        "metadataPlaceholder",
        "reviewRequired",
    ]
    bounds: Rect
    source_element_id: str | None = Field(default=None, max_length=240)
    classification: Literal["confirmed", "projected", "reviewRequired"]


class SlideRolePolicySnapshot(StrictCamelModel):
    """Hidden company consistency rules; never a user-facing third selector."""

    role: Literal["cover", "section", "content", "closing"]
    title_anchor: Rect
    content_rect: Rect
    max_title_lines: int = Field(ge=1, le=4)
    title_alignment: Literal["left", "center", "right"]
    bullet_policy: Literal["none", "preserve", "normalizeOneLevel"]
    show_document_id: bool = False


class DeckMetadataSnapshot(StrictCamelModel):
    """Single authoritative RFP/document identity frozen at admission."""

    document_id: str | None = Field(default=None, max_length=500)
    kind: Literal["none", "rfp", "documentNumber"] = "none"

    @model_validator(mode="after")
    def _kind_matches_value(self) -> "DeckMetadataSnapshot":
        if (self.kind == "none") != (self.document_id is None):
            raise ValueError("document metadata kind and value must match")
        return self


class ConsistencyPolicySnapshot(StrictCamelModel):
    """Content-addressed projection of company furniture and document rules."""

    schema_version: Literal["company-consistency-policy/v1"] = (
        "company-consistency-policy/v1"
    )
    source_master_digest: Digest
    content_rect: Rect
    reserved_zones: list[ReservedZoneSnapshot] = Field(max_length=4096)
    role_policies: list[SlideRolePolicySnapshot] = Field(min_length=4, max_length=4)
    metadata: DeckMetadataSnapshot = Field(default_factory=DeckMetadataSnapshot)
    review_required_element_ids: list[str] = Field(default_factory=list, max_length=2048)
    digest: Digest

    @model_validator(mode="after")
    def _policy_is_complete_and_sealed(self) -> "ConsistencyPolicySnapshot":
        if {item.role for item in self.role_policies} != {
            "cover",
            "section",
            "content",
            "closing",
        }:
            raise ValueError("consistency policy requires all four slide roles")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        if self.digest != _canonical_digest(payload):
            raise ValueError("consistency policy digest mismatch")
        return self


class AuthoredTemplateSnapshot(StrictCamelModel):
    """Retained authored-template compiler contract frozen at admission."""

    schema_version: Literal["authored-template-snapshot/v1"] = (
        "authored-template-snapshot/v1"
    )
    style_id: str | None = Field(default=None, max_length=200)
    style_digest: Digest | None = None
    retained_reference_id: str = Field(min_length=1, max_length=240)
    archetypes: list[str] = Field(min_length=1, max_length=32)
    semantic_roles: list[str] = Field(min_length=1, max_length=64)
    resolved_reference_layout: "ResolvedReferenceLayoutSnapshot | None" = None
    resolved_reference_layout_digest: Digest | None = None
    digest: Digest

    @model_validator(mode="after")
    def _template_is_sealed(self) -> "AuthoredTemplateSnapshot":
        if (self.style_id is None) != (self.style_digest is None):
            raise ValueError("authored template style identity must be paired")
        if (self.resolved_reference_layout is None) != (
            self.resolved_reference_layout_digest is None
        ):
            raise ValueError("resolved reference layout and digest must be paired")
        if self.resolved_reference_layout is not None:
            if (
                self.resolved_reference_layout.retained_reference_id
                != self.retained_reference_id
            ):
                raise ValueError("resolved reference layout identity mismatch")
            if (
                self.resolved_reference_layout.digest
                != self.resolved_reference_layout_digest
            ):
                raise ValueError("resolved reference layout digest mismatch")
            layout_archetypes = {
                item.get("archetype")
                for item in self.resolved_reference_layout.layouts
            }
            if not set(self.archetypes).issubset(layout_archetypes):
                raise ValueError(
                    "resolved reference layout does not cover template archetypes"
                )
            resolved_semantic_roles = {
                block.get("semanticRole")
                for layout in self.resolved_reference_layout.layouts
                for block in layout.get("blocks", [])
                if isinstance(block, dict)
            }
            if not set(self.semantic_roles).issubset(resolved_semantic_roles):
                raise ValueError(
                    "resolved reference layout does not cover semantic roles"
                )
        payload = self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        if self.digest != _canonical_digest(payload):
            raise ValueError("authored template digest mismatch")
        return self


class ReferenceLayoutCanvasSnapshot(StrictCamelModel):
    """Canvas frozen with the retained reference geometry."""

    width: float = Field(gt=0, le=100_000)
    height: float = Field(gt=0, le=100_000)


class ResolvedReferenceLayoutSnapshot(StrictCamelModel):
    """Full retained-reference layout library persisted in the composition pin.

    Layout records deliberately stay canonical JSON objects: their shape vocabulary is
    versioned by ``schemaVersion`` and is consumed identically by the HTML and OOXML
    compilers.  The outer envelope still validates identity, canvas, count, and digest,
    so a future layout vocabulary can be replayed without silently discarding fields.
    """

    schema_version: Literal["retained-reference-layout/v1"] = (
        "retained-reference-layout/v1"
    )
    retained_reference_id: str = Field(min_length=1, max_length=240)
    source_digest: Digest
    canvas: ReferenceLayoutCanvasSnapshot
    layouts: list[dict[str, Any]] = Field(min_length=1, max_length=128)
    digest: Digest

    @model_validator(mode="after")
    def _resolved_layout_is_sealed(self) -> "ResolvedReferenceLayoutSnapshot":
        ids: list[str] = []
        for index, layout in enumerate(self.layouts):
            layout_id = layout.get("id")
            archetype = layout.get("archetype")
            slide_role = layout.get("slideRole")
            if not isinstance(layout_id, str) or not layout_id:
                raise ValueError(f"resolved reference layout {index} requires id")
            if not isinstance(archetype, str) or not archetype:
                raise ValueError(
                    f"resolved reference layout {index} requires archetype"
                )
            if slide_role not in {"cover", "section", "content", "closing"}:
                raise ValueError(
                    f"resolved reference layout {index} has invalid slideRole"
                )
            if not isinstance(layout.get("title"), dict):
                raise ValueError(f"resolved reference layout {index} requires title")
            if not isinstance(layout.get("contentRect"), dict):
                raise ValueError(
                    f"resolved reference layout {index} requires contentRect"
                )
            if not isinstance(layout.get("blocks"), list):
                raise ValueError(f"resolved reference layout {index} requires blocks")
            if not isinstance(layout.get("decorations"), list):
                raise ValueError(
                    f"resolved reference layout {index} requires decorations"
                )
            ids.append(layout_id)
        if len(ids) != len(set(ids)):
            raise ValueError("resolved reference layout ids must be unique")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"digest"})
        if self.digest != _canonical_digest(payload):
            raise ValueError("resolved reference layout snapshot digest mismatch")
        return self


def seal_consistency_policy_snapshot(**values: object) -> ConsistencyPolicySnapshot:
    payload = ConsistencyPolicySnapshot.model_construct(
        **values,
        digest="sha256:" + "0" * 64,
    ).model_dump(mode="json", by_alias=True, exclude={"digest"})
    return ConsistencyPolicySnapshot(**values, digest=_canonical_digest(payload))


def seal_authored_template_snapshot(**values: object) -> AuthoredTemplateSnapshot:
    payload = AuthoredTemplateSnapshot.model_construct(
        **values,
        digest="sha256:" + "0" * 64,
    ).model_dump(mode="json", by_alias=True, exclude={"digest"})
    return AuthoredTemplateSnapshot(**values, digest=_canonical_digest(payload))


def composition_digest(
    *,
    master_digest: str,
    style_digest: str | None,
    merge_policy_version: str,
    style_id: str | None,
    body_visual: BodyVisualSnapshot,
    effective_contract_digest: str,
    consistency_policy: ConsistencyPolicySnapshot | None = None,
    authored_template: AuthoredTemplateSnapshot | None = None,
) -> str:
    """Seal the composition inputs and their materialized effective contract."""

    payload = {
        "bodyVisual": body_visual.model_dump(mode="json", by_alias=True),
        "effectiveContractDigest": effective_contract_digest,
        "masterDigest": master_digest,
        "mergePolicyVersion": merge_policy_version,
        "schemaVersion": "authored-composition-pin/v1",
        "styleDigest": style_digest,
        "styleId": style_id,
    }
    if consistency_policy is not None:
        payload["consistencyPolicy"] = consistency_policy.model_dump(
            mode="json", by_alias=True
        )
    if authored_template is not None:
        payload["authoredTemplate"] = authored_template.model_dump(
            mode="json", by_alias=True
        )
    return _canonical_digest(payload)


class AuthoredCompositionPin(StrictCamelModel):
    """Sole content-addressed composition used by every generation consumer.

    The authored style and company standard remain independently addressable
    through the two typed projections below, while this pin and its digest are
    the only durable preview/checkpoint/publication/export authority.
    """

    schema_version: Literal["authored-composition-pin/v1"] = (
        "authored-composition-pin/v1"
    )
    master_digest: Digest
    style_digest: Digest | None = None
    merge_policy_version: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    style_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    body_visual: BodyVisualSnapshot
    consistency_policy: ConsistencyPolicySnapshot | None = None
    authored_template: AuthoredTemplateSnapshot | None = None
    effective_contract_digest: Digest
    digest: Digest

    @model_validator(mode="after")
    def _digests_seal_the_materialized_contract(self) -> "AuthoredCompositionPin":
        if (self.style_id is None) != (self.style_digest is None):
            raise ValueError("styleId and styleDigest must be present together")
        if (self.consistency_policy is None) != (self.authored_template is None):
            raise ValueError(
                "company consistency policy and authored template must be frozen together"
            )
        if self.consistency_policy is not None:
            if self.consistency_policy.source_master_digest != self.master_digest:
                raise ValueError("company standard master digest mismatch")
            if (
                self.authored_template.style_id != self.style_id
                or self.authored_template.style_digest != self.style_digest
            ):
                raise ValueError("authored style snapshot identity mismatch")
        expected_composition = composition_digest(
            master_digest=self.master_digest,
            style_digest=self.style_digest,
            merge_policy_version=self.merge_policy_version,
            style_id=self.style_id,
            body_visual=self.body_visual,
            effective_contract_digest=self.effective_contract_digest,
            consistency_policy=self.consistency_policy,
            authored_template=self.authored_template,
        )
        if self.digest != expected_composition:
            raise ValueError("composition digest does not seal its input digests")
        return self

    @property
    def authored_style_pin(self) -> AuthoredStylePin:
        """Return the independently-addressed style input sealed by this pin."""

        values = {
            "style_id": self.style_id,
            "style_digest": self.style_digest,
            "body_visual": self.body_visual,
            "authored_template_digest": (
                self.authored_template.digest
                if self.authored_template is not None
                else None
            ),
        }
        payload = AuthoredStylePin.model_construct(
            **values,
            digest="sha256:" + "0" * 64,
        ).model_dump(mode="json", by_alias=True, exclude={"digest"})
        return AuthoredStylePin(**values, digest=_canonical_digest(payload))

    @property
    def company_standard_pin(self) -> CompanyStandardPin:
        """Return the independently-addressed company input sealed by this pin."""

        values = {
            "master_digest": self.master_digest,
            "consistency_policy_digest": (
                self.consistency_policy.digest
                if self.consistency_policy is not None
                else None
            ),
            "effective_contract_digest": self.effective_contract_digest,
        }
        payload = CompanyStandardPin.model_construct(
            **values,
            digest="sha256:" + "0" * 64,
        ).model_dump(mode="json", by_alias=True, exclude={"digest"})
        return CompanyStandardPin(**values, digest=_canonical_digest(payload))

    def require_digest(self, expected_digest: str, *, consumer: str) -> None:
        """Fail closed when a consumer attempts to use another composition."""

        if self.digest != expected_digest:
            raise ValueError(f"{consumer} composition digest mismatch")


def seal_authored_composition_pin(
    *,
    master_digest: str,
    style_digest: str | None,
    merge_policy_version: str,
    body_visual: BodyVisualSnapshot,
    effective_contract_digest: str,
    style_id: str | None = None,
    consistency_policy: ConsistencyPolicySnapshot | None = None,
    authored_template: AuthoredTemplateSnapshot | None = None,
) -> AuthoredCompositionPin:
    """Build a validated immutable pin without consulting mutable style sources."""

    return AuthoredCompositionPin(
        master_digest=master_digest,
        style_digest=style_digest,
        merge_policy_version=merge_policy_version,
        style_id=style_id,
        body_visual=body_visual,
        consistency_policy=consistency_policy,
        authored_template=authored_template,
        effective_contract_digest=effective_contract_digest,
        digest=composition_digest(
            master_digest=master_digest,
            style_digest=style_digest,
            merge_policy_version=merge_policy_version,
            style_id=style_id,
            body_visual=body_visual,
            effective_contract_digest=effective_contract_digest,
            consistency_policy=consistency_policy,
            authored_template=authored_template,
        ),
    )
