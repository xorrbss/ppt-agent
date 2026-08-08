"""Closed, canonical contracts for SlideSpec 1.x and its repair pipeline."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    model_validator,
)

SLIDESPEC_SCHEMA_V1_0 = "urn:ppt-agent:slidespec:1.0"
SLIDESPEC_SCHEMA = "urn:ppt-agent:slidespec:1.1"
REPAIR_PLAN_SCHEMA = "urn:ppt-agent:repair-plan:1.0"
LAYOUT_PLAN_SCHEMA = "urn:ppt-agent:layout-plan:1.0"
ACCEPTANCE_SCHEMA = "urn:ppt-agent:acceptance-decision:1.0"

SemanticId = Annotated[
    str, StringConstraints(pattern=r"^sem:[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}$")
]
LayoutId = Annotated[
    str, StringConstraints(pattern=r"^lay:[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
TokenName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
]
ReasonCode = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
]
ConstraintId = Annotated[
    str, StringConstraints(pattern=r"^con:[A-Za-z0-9][A-Za-z0-9._:/-]{0,126}$")
]

class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        allow_inf_nan=False,
    )

class ImportantToken(StrictModel):
    name: TokenName
    value: str | int | float | bool
    important: bool = True

class Relationship(StrictModel):
    kind: Annotated[str, StringConstraints(min_length=1, max_length=48)]
    targetSemanticId: SemanticId

class ContentNode(StrictModel):
    semanticId: SemanticId
    kind: Literal[
        "title",
        "text",
        "list",
        "metric",
        "table",
        "chart",
        "image",
        "shape",
        "connector",
        "group",
    ]
    text: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    parentSemanticId: SemanticId | None = None
    readingOrder: Annotated[int, Field(ge=0)] = 0
    importantTokens: tuple[TokenName, ...] = ()
    relationships: tuple[Relationship, ...] = ()

class Insets(StrictModel):
    top: Annotated[float, Field(ge=0, le=1)] = 0
    right: Annotated[float, Field(ge=0, le=1)] = 0
    bottom: Annotated[float, Field(ge=0, le=1)] = 0
    left: Annotated[float, Field(ge=0, le=1)] = 0

class Point(StrictModel):
    x: Annotated[float, Field(ge=-1, le=1)]
    y: Annotated[float, Field(ge=-1, le=1)]

class FocalPoint(StrictModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]

class Bounds(StrictModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def contained(self) -> "Bounds":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bounds must remain inside the normalized parent")
        return self

class GridPlacement(StrictModel):
    row: Annotated[int, Field(ge=0, le=63)]
    column: Annotated[int, Field(ge=0, le=63)]
    rowSpan: Annotated[int, Field(ge=1, le=64)] = 1
    columnSpan: Annotated[int, Field(ge=1, le=64)] = 1

class TrackSize(StrictModel):
    unit: Literal["fraction", "content", "normalized"]
    value: Annotated[float, Field(gt=0, le=64)]

class ConnectorRoute(StrictModel):
    points: Annotated[tuple[FocalPoint, ...], Field(min_length=2, max_length=16)]

RepairOperationName = Literal[
    "setSpacing", "setPadding", "setAlignment", "setJustification",
    "setTrackSize", "setColumnRatio", "setGridPlacement", "setBounds",
    "translateWithinParent", "resizeWithinBounds", "setFontScale",
    "setLineHeightToken", "setTextFitPolicy", "setImageFit",
    "setCropFocalPoint", "rerouteConnector", "replaceLayoutSubtree",
]

class LayoutIntent(StrictModel):
    """Design meaning that a geometry-only repair cannot safely infer."""

    role: Literal[
        "primary", "supporting", "navigation", "annotation", "decoration"
    ] = "supporting"
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    alignmentGroup: TokenName | None = None
    intentionalOverlapGroup: TokenName | None = None
    allowIntentionalOverlap: bool = False

    @model_validator(mode="after")
    def valid_overlap_intent(self) -> "LayoutIntent":
        if self.intentionalOverlapGroup is not None and not self.allowIntentionalOverlap:
            raise ValueError(
                "intentionalOverlapGroup requires allowIntentionalOverlap=true"
            )
        return self

class LayoutConstraints(StrictModel):
    """Closed mutation envelope enforced before a repair candidate can render."""

    locked: bool = False
    allowedOperations: tuple[RepairOperationName, ...] | None = None
    lockPosition: bool = False
    lockSize: bool = False
    preserveAspectRatio: bool = False
    minWidth: Annotated[float, Field(gt=0, le=1)] = 1e-6
    minHeight: Annotated[float, Field(gt=0, le=1)] = 1e-6
    maxWidth: Annotated[float, Field(gt=0, le=1)] = 1
    maxHeight: Annotated[float, Field(gt=0, le=1)] = 1
    minFontScale: Annotated[float, Field(ge=0.5, le=2)] = 0.85
    maxFontShrink: Annotated[float, Field(ge=0, le=1)] = 0.15
    maxTranslation: Annotated[float, Field(ge=0, le=1)] = 0.08
    maxResizeDelta: Annotated[float, Field(ge=0, le=1)] = 0.25

    @model_validator(mode="after")
    def unique_operations(self) -> "LayoutConstraints":
        if self.allowedOperations is not None and len(self.allowedOperations) != len(
            set(self.allowedOperations)
        ):
            raise ValueError("allowedOperations must not contain duplicates")
        if self.minWidth > self.maxWidth or self.minHeight > self.maxHeight:
            raise ValueError("minimum layout size must not exceed maximum layout size")
        return self

class LayoutNode(StrictModel):
    layoutId: LayoutId
    kind: Literal["canvas", "stack", "grid", "frame", "content", "decoration"]
    contentSemanticId: SemanticId | None = None
    children: tuple["LayoutNode", ...] = ()
    bounds: Bounds | None = None
    spacing: Annotated[float, Field(ge=0, le=512)] = 0
    padding: Insets = Field(default_factory=Insets)
    alignment: Literal["start", "center", "end", "stretch"] = "stretch"
    justification: Literal["start", "center", "end", "space-between"] = "start"
    tracks: tuple[TrackSize, ...] = ()
    columnRatio: Annotated[tuple[float, ...], Field(max_length=16)] = ()
    gridPlacement: GridPlacement | None = None
    fontScale: Annotated[float, Field(ge=0.5, le=2)] = 1
    lineHeightToken: TokenName | None = None
    textFitPolicy: Literal["none", "shrink", "wrap", "truncate"] = "wrap"
    imageFit: Literal["contain", "cover", "fill"] = "contain"
    cropFocalPoint: FocalPoint = Field(default_factory=lambda: FocalPoint(x=0.5, y=0.5))
    connectorRoute: ConnectorRoute | None = None
    intent: LayoutIntent | None = None
    constraints: LayoutConstraints | None = None

    @model_validator(mode="after")
    def valid_shape(self) -> "LayoutNode":
        if self.contentSemanticId is not None and self.children:
            raise ValueError("a content binding cannot also own layout children")
        if self.columnRatio and any(value <= 0 for value in self.columnRatio):
            raise ValueError("column ratios must be positive")
        return self


class ConstraintBase(StrictModel):
    constraintId: ConstraintId
    strength: Literal["hard", "soft"] = "hard"
    priority: Annotated[int, Field(ge=1, le=100)] = 100
    confidence: Annotated[float, Field(ge=0, le=1)] = 1
    source: Literal["author", "imported", "repair"] = "author"


class GroupConstraint(ConstraintBase):
    kind: Literal["group"]
    members: Annotated[tuple[LayoutId, ...], Field(min_length=2, max_length=128)]
    atomicTranslation: bool = True
    preserveInternalOrder: bool = True
    coordinatedScale: bool = True
    coordinatedRotation: bool = True

    @model_validator(mode="after")
    def unique_members(self) -> "GroupConstraint":
        if len(self.members) != len(set(self.members)):
            raise ValueError("group members must be unique")
        return self


class AnchorConstraint(ConstraintBase):
    kind: Literal["anchor"]
    target: LayoutId
    container: LayoutId
    targetEdge: Literal["left", "centerX", "right", "top", "centerY", "bottom"]
    containerEdge: Literal["left", "centerX", "right", "top", "centerY", "bottom"]
    offset: Annotated[float, Field(ge=-1, le=1)] = 0
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.005


class AlignmentConstraint(ConstraintBase):
    kind: Literal["alignment"]
    targets: Annotated[tuple[LayoutId, ...], Field(min_length=2, max_length=128)]
    edge: Literal["left", "centerX", "right", "top", "centerY", "bottom"]
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.005

    @model_validator(mode="after")
    def unique_targets(self) -> "AlignmentConstraint":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("alignment targets must be unique")
        return self


class GapConstraint(ConstraintBase):
    kind: Literal["gap"]
    leading: LayoutId
    trailing: LayoutId
    axis: Literal["horizontal", "vertical"]
    minimum: Annotated[float, Field(ge=0, le=1)] = 0
    maximum: Annotated[float, Field(ge=0, le=1)] | None = None
    preferred: Annotated[float, Field(ge=0, le=1)] | None = None
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.005

    @model_validator(mode="after")
    def valid_range(self) -> "GapConstraint":
        if self.leading == self.trailing:
            raise ValueError("gap endpoints must differ")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("gap maximum must be at least minimum")
        if self.preferred is not None and not (
            self.minimum <= self.preferred <= (self.maximum or 1)
        ):
            raise ValueError("preferred gap must be inside the allowed range")
        return self


class ContainmentConstraint(ConstraintBase):
    kind: Literal["containment"]
    targets: Annotated[tuple[LayoutId, ...], Field(min_length=1, max_length=128)]
    container: LayoutId
    insets: Insets = Field(default_factory=Insets)
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.002

    @model_validator(mode="after")
    def unique_targets(self) -> "ContainmentConstraint":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("containment targets must be unique")
        if self.container in self.targets:
            raise ValueError("containment target cannot contain itself")
        return self


class NonOverlapConstraint(ConstraintBase):
    kind: Literal["nonOverlap"]
    targets: Annotated[tuple[LayoutId, ...], Field(min_length=2, max_length=128)]
    clearance: Annotated[float, Field(ge=0, le=1)] = 0
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.002

    @model_validator(mode="after")
    def unique_targets(self) -> "NonOverlapConstraint":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("nonOverlap targets must be unique")
        return self


class OrderConstraint(ConstraintBase):
    kind: Literal["order"]
    before: LayoutId
    after: LayoutId
    orderBy: Literal["reading", "x", "y", "z"] = "reading"
    minimumSeparation: Annotated[float, Field(ge=0, le=1)] = 0
    tolerance: Annotated[float, Field(ge=0, le=0.25)] = 0.002

    @model_validator(mode="after")
    def different_targets(self) -> "OrderConstraint":
        if self.before == self.after:
            raise ValueError("order endpoints must differ")
        return self


class ReadabilityConstraint(ConstraintBase):
    kind: Literal["readability"]
    targets: Annotated[tuple[LayoutId, ...], Field(min_length=1, max_length=128)]
    minFontSizePx: Annotated[float, Field(gt=0, le=512)] | None = None
    minLineHeightRatio: Annotated[float, Field(ge=0.5, le=4)] | None = None
    minContrastRatio: Annotated[float, Field(ge=1, le=21)] | None = None
    noTruncation: bool = True
    requireVisible: bool = True

    @model_validator(mode="after")
    def useful_and_unique(self) -> "ReadabilityConstraint":
        if len(self.targets) != len(set(self.targets)):
            raise ValueError("readability targets must be unique")
        if not any(
            (
                self.minFontSizePx is not None,
                self.minLineHeightRatio is not None,
                self.minContrastRatio is not None,
                self.noTruncation,
                self.requireVisible,
            )
        ):
            raise ValueError("readability constraint must enforce at least one rule")
        return self


SlideConstraint = Annotated[
    GroupConstraint
    | AnchorConstraint
    | AlignmentConstraint
    | GapConstraint
    | ContainmentConstraint
    | NonOverlapConstraint
    | OrderConstraint
    | ReadabilityConstraint,
    Field(discriminator="kind"),
]


class ConstraintPolicy(StrictModel):
    hardConflict: Literal["reject_candidate"] = "reject_candidate"
    softConflict: Literal["minimize_weighted_violation"] = (
        "minimize_weighted_violation"
    )
    unknownHardEvidence: Literal["reject_candidate"] = "reject_candidate"
    hardDominatesSoft: Literal[True] = True

class SlideDigests(StrictModel):
    content: Digest
    hierarchy: Digest
    importantTokens: Digest
    spec: Digest

class Provenance(StrictModel):
    source: Literal["native", "adaptive-adapter", "authored-html-import", "repair"] = "native"
    parentSpecHash: Digest | None = None
    adapterVersion: str | None = None

class SlideSpec(StrictModel):
    schemaUri: Literal[SLIDESPEC_SCHEMA_V1_0, SLIDESPEC_SCHEMA] = Field(alias="$schema")
    specRevision: Annotated[int, Field(ge=1)]
    slideId: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    content: Annotated[tuple[ContentNode, ...], Field(min_length=1, max_length=512)]
    layout: LayoutNode
    tokens: Annotated[tuple[ImportantToken, ...], Field(max_length=256)] = ()
    constraints: Annotated[tuple[SlideConstraint, ...], Field(max_length=1024)] = ()
    constraintPolicy: ConstraintPolicy = Field(default_factory=ConstraintPolicy)
    provenance: Provenance = Field(default_factory=Provenance)
    digests: SlideDigests

    @model_validator(mode="after")
    def versioned_layout_contract(self) -> "SlideSpec":
        stack = [self.layout]
        while stack:
            node = stack.pop()
            if self.schemaUri == SLIDESPEC_SCHEMA_V1_0 and (
                node.intent is not None or node.constraints is not None
            ):
                raise ValueError(
                    "SlideSpec 1.0 cannot carry SlideSpec 1.1 layout metadata"
                )
            stack.extend(node.children)
        if self.schemaUri == SLIDESPEC_SCHEMA_V1_0 and self.constraints:
            raise ValueError("SlideSpec 1.0 cannot carry relational constraints")
        return self

class LayoutPlan(StrictModel):
    schemaUri: Literal[LAYOUT_PLAN_SCHEMA] = Field(alias="$schema")
    root: LayoutNode

RepairTarget = SemanticId | LayoutId

class OperationPrecondition(StrictModel):
    expectedLayoutHash: Digest | None = None
    expectedBounds: Bounds | None = Field(
        default=None, validation_alias=AliasChoices("expectedBounds", "currentBounds")
    )
    expectedValue: JsonValue | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "expectedValue", "currentGap", "currentPadding", "currentScale",
            "currentSize", "currentRatio",
        ),
    )

class OperationBase(StrictModel):
    target: RepairTarget
    precondition: OperationPrecondition | None = None

class SetSpacingOperation(OperationBase):
    op: Literal["setSpacing"]
    gap: Annotated[float, Field(ge=0, le=512)] = Field(
        validation_alias=AliasChoices("gap", "value")
    )

class SetPaddingOperation(OperationBase):
    op: Literal["setPadding"]
    padding: Insets = Field(validation_alias=AliasChoices("padding", "value"))

class SetFlowOperation(OperationBase):
    op: Literal["setAlignment", "setJustification"]
    value: Literal["start", "center", "end", "stretch", "space-between"]

    @model_validator(mode="after")
    def compatible_value(self) -> "SetFlowOperation":
        if self.op == "setAlignment" and self.value == "space-between":
            raise ValueError("space-between is not an alignment")
        if self.op == "setJustification" and self.value == "stretch":
            raise ValueError("stretch is not a justification")
        return self

class SetTracksOperation(OperationBase):
    op: Literal["setTrackSize"]
    value: Annotated[tuple[TrackSize, ...], Field(min_length=1, max_length=16)]

class SetColumnRatioOperation(OperationBase):
    op: Literal["setColumnRatio"]
    value: Annotated[tuple[float, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def positive(self) -> "SetColumnRatioOperation":
        if any(value <= 0 for value in self.value):
            raise ValueError("column ratios must be positive")
        return self

class SetGridPlacementOperation(OperationBase):
    op: Literal["setGridPlacement"]
    value: GridPlacement

class SetBoundsOperation(OperationBase):
    op: Literal["setBounds"]
    bounds: Bounds = Field(validation_alias=AliasChoices("bounds", "value"))

class TranslateOperation(OperationBase):
    op: Literal["translateWithinParent"]
    value: Point

class ResizeOperation(OperationBase):
    op: Literal["resizeWithinBounds"]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]

class FontScaleBounds(StrictModel):
    minPx: Annotated[float, Field(gt=0)] | None = None
    maxPx: Annotated[float, Field(gt=0)] | None = None
    minScale: Annotated[float, Field(ge=0.5, le=2)] | None = None
    maxScale: Annotated[float, Field(ge=0.5, le=2)] | None = None

    @model_validator(mode="after")
    def ordered(self) -> "FontScaleBounds":
        for low, high in ((self.minPx, self.maxPx), (self.minScale, self.maxScale)):
            if low is not None and high is not None and low > high:
                raise ValueError("font scale bounds must be ordered")
        return self

class SetFontScaleOperation(OperationBase):
    op: Literal["setFontScale"]
    scale: Annotated[float, Field(ge=0.5, le=2)] = Field(
        validation_alias=AliasChoices("scale", "value")
    )
    bounds: FontScaleBounds | None = None

class SetTokenOperation(OperationBase):
    op: Literal["setLineHeightToken"]
    value: TokenName

class SetTextFitOperation(OperationBase):
    op: Literal["setTextFitPolicy"]
    value: Literal["none", "shrink", "wrap", "truncate"]

class SetImageFitOperation(OperationBase):
    op: Literal["setImageFit"]
    value: Literal["contain", "cover", "fill"]

class SetCropOperation(OperationBase):
    op: Literal["setCropFocalPoint"]
    value: FocalPoint

class RerouteOperation(OperationBase):
    op: Literal["rerouteConnector"]
    value: ConnectorRoute

class ReplaceLayoutOperation(OperationBase):
    op: Literal["replaceLayoutSubtree"]
    value: LayoutPlan

RepairOperation = Annotated[
    SetSpacingOperation
    | SetPaddingOperation
    | SetFlowOperation
    | SetTracksOperation
    | SetColumnRatioOperation
    | SetGridPlacementOperation
    | SetBoundsOperation
    | TranslateOperation
    | ResizeOperation
    | SetFontScaleOperation
    | SetTokenOperation
    | SetTextFitOperation
    | SetImageFitOperation
    | SetCropOperation
    | RerouteOperation
    | ReplaceLayoutOperation,
    Field(discriminator="op"),
]

class RepairBudget(StrictModel):
    maxOperations: Annotated[int, Field(ge=1, le=32)] = 12
    maxTouchedLayoutNodes: Annotated[int, Field(ge=1, le=32)] = Field(
        default=8,
        validation_alias=AliasChoices("maxTouchedLayoutNodes", "maxAffectedBlocks"),
    )
    maxLayoutDepth: Annotated[int, Field(ge=1, le=32)] = 12

class RepairProvenance(StrictModel):
    source: Literal["deterministic", "llm"] = Field(
        validation_alias=AliasChoices("source", "kind"), serialization_alias="kind"
    )
    policy_version: Annotated[str, StringConstraints(min_length=1, max_length=128)] = Field(
        alias="policyVersion"
    )
    cost_version: str | None = Field(default=None, alias="costVersion")
    prompt_version: str | None = Field(default=None, alias="promptVersion")

    @model_validator(mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("kind") == "rule_engine":
            value = {**value, "kind": "deterministic"}
        return value

    @field_serializer("source")
    def serialize_kind(self, value: str) -> str:
        return "rule_engine" if value == "deterministic" else value

class RepairPreconditions(StrictModel):
    slide_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] = Field(
        alias="slideId"
    )
    spec_revision: Annotated[int, Field(ge=1)] = Field(alias="baseSpecRevision")
    spec_hash: Digest = Field(alias="baseSpecHash")
    content_digest: Digest = Field(alias="expectedContentDigest")

class RepairPlan(StrictModel):
    schema_uri: Literal[REPAIR_PLAN_SCHEMA] = Field(alias="$schema")
    plan_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] = Field(
        alias="planId"
    )
    slide_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] = Field(
        alias="slideId"
    )
    base_spec_revision: Annotated[int, Field(ge=1)] = Field(alias="baseSpecRevision")
    base_spec_hash: Digest = Field(alias="baseSpecHash")
    expected_content_digest: Digest = Field(alias="expectedContentDigest")
    scope: Annotated[tuple[RepairTarget, ...], Field(max_length=32)] = ()
    diagnostic_refs: Annotated[tuple[str, ...], Field(max_length=64)] = Field(
        default=(), alias="diagnosticRefs"
    )
    strategy: Literal["deterministic_cascade", "minimal_change", "layout_reflow"]
    operations: Annotated[tuple[RepairOperation, ...], Field(min_length=1, max_length=32)]
    budget: RepairBudget = Field(
        default_factory=RepairBudget,
        validation_alias=AliasChoices("budget", "limits"),
        serialization_alias="limits",
    )
    preconditions: RepairPreconditions | None = None
    provenance: RepairProvenance = Field(
        validation_alias=AliasChoices("provenance", "proposedBy"),
        serialization_alias="proposedBy",
    )

    @model_validator(mode="after")
    def preconditions_match(self) -> "RepairPlan":
        if self.preconditions is None:
            return self
        expected = (
            self.slide_id,
            self.base_spec_revision,
            self.base_spec_hash,
            self.expected_content_digest,
        )
        actual = (
            self.preconditions.slide_id,
            self.preconditions.spec_revision,
            self.preconditions.spec_hash,
            self.preconditions.content_digest,
        )
        if actual != expected:
            raise ValueError("repair preconditions must match the bound plan identity")
        return self

class AppliedRepair(StrictModel):
    spec: SlideSpec
    touchedLayoutIds: tuple[LayoutId, ...]

class StageDecision(StrictModel):
    stage: Literal["schema", "invariant", "render", "dom", "ocr", "vqa", "publication"]
    status: Literal["pass", "fail", "inconclusive", "skipped", "not_started"]
    reason_codes: tuple[ReasonCode, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")

    @model_validator(mode="after")
    def failure_has_reason(self) -> "StageDecision":
        if self.status in {"fail", "inconclusive"} and not self.reason_codes:
            raise ValueError("failed stages require a stable reason code")
        return self

class AcceptanceDecision(StrictModel):
    schema_uri: Literal[ACCEPTANCE_SCHEMA] = Field(alias="$schema")
    decision_id: str | None = Field(default=None, alias="decisionId")
    plan_id: str | None = Field(default=None, alias="planId")
    base_spec_revision: Annotated[int, Field(ge=1)] | None = Field(
        default=None, alias="baseSpecRevision"
    )
    base_spec_hash: Digest = Field(alias="baseSpecHash")
    candidate_spec_hash: Digest | None = Field(default=None, alias="candidateSpecHash")
    trigger_type: Literal["geometry", "content_visibility", "visual_quality"] = Field(
        alias="triggerType"
    )
    gate_policy: str = Field(default="slidespec-acceptance/1.0", alias="gatePolicy")
    required_gates: tuple[
        Literal["schema", "invariant", "render", "dom", "ocr", "vqa", "publication"],
        ...,
    ] = Field(
        default=("schema", "invariant", "render", "dom", "ocr", "vqa"),
        alias="requiredGates",
    )
    stages: Annotated[tuple[StageDecision, ...], Field(min_length=1, max_length=7)]
    outcome: Literal["accepted", "rejected", "inconclusive"]
    reason_codes: tuple[ReasonCode, ...] = Field(alias="reasonCodes")
    apply_status: Literal["not_started"] = Field(default="not_started", alias="applyStatus")
    publish_status: Literal["not_started"] = Field(default="not_started", alias="publishStatus")
    post_verify_status: Literal["not_started"] = Field(
        default="not_started", alias="postVerifyStatus"
    )
    policy_version: str = Field(default="slidespec-acceptance/1.0", alias="policyVersion")

    @model_validator(mode="after")
    def consistent(self) -> "AcceptanceDecision":
        names = [stage.stage for stage in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("acceptance stages must be unique")
        by_name = {stage.stage: stage for stage in self.stages}
        required = [by_name.get(name) for name in self.required_gates]
        failed = any(stage is not None and stage.status == "fail" for stage in required)
        unresolved = any(
            stage is None or stage.status in {"inconclusive", "skipped", "not_started"}
            for stage in required
        )
        if self.outcome == "accepted" and (failed or unresolved):
            raise ValueError("accepted decisions require every required gate to pass")
        if self.outcome == "rejected" and not failed:
            raise ValueError("rejected decisions require a failed gate")
        if self.outcome == "inconclusive" and (failed or not unresolved):
            raise ValueError("inconclusive decisions require an unresolved gate")
        if self.outcome == "accepted" and self.candidate_spec_hash is None:
            raise ValueError("accepted candidates require a spec hash")
        if not self.reason_codes:
            raise ValueError("acceptance decisions require stable reason codes")
        return self

class RendererManifest(StrictModel):
    name: str
    version: str
    runtime: str

class FontManifest(StrictModel):
    digest: Digest
    fonts: tuple[str, ...] = ()

class AssetManifest(StrictModel):
    digest: Digest
    assets: tuple[str, ...] = ()


class ImportedPixelBounds(StrictModel):
    x: Annotated[float, Field(ge=-65536, le=65536)]
    y: Annotated[float, Field(ge=-65536, le=65536)]
    width: Annotated[float, Field(ge=0, le=65536)]
    height: Annotated[float, Field(ge=0, le=65536)]


class ImportedHtmlBinding(StrictModel):
    """Immutable bridge from one canonical layout node to authored HTML."""

    semanticId: SemanticId
    layoutId: LayoutId
    qid: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    tag: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
    bounds: Bounds
    sourceBoundsPx: ImportedPixelBounds
    fontSizePx: Annotated[float, Field(gt=0, le=512)] | None = None
    position: Literal["static", "relative", "absolute", "fixed", "sticky"] = "static"
    # ``None`` means that an older artifact did not capture the computed
    # transform.  Static-position projection is allowed only for newly measured
    # bindings whose value is exactly ``none``; treating missing telemetry as
    # composable would make legacy bundles unsafe.
    transform: Annotated[str, StringConstraints(min_length=1, max_length=512)] | None = None
    # True only when the importer observed a real Chrome paint-only translation
    # probe with unchanged parent/sibling geometry.  Older artifacts therefore
    # retain the conservative fail-closed behavior.
    staticTranslationSafe: bool = False
    leftPx: Annotated[float, Field(ge=-65536, le=65536)] | None = None
    topPx: Annotated[float, Field(ge=-65536, le=65536)] | None = None
    rightPx: Annotated[float, Field(ge=-65536, le=65536)] | None = None
    bottomPx: Annotated[float, Field(ge=-65536, le=65536)] | None = None
    display: str = "block"
    gapPx: Annotated[float, Field(ge=0, le=4096)] = 0
    paddingTopPx: Annotated[float, Field(ge=0, le=4096)] = 0
    paddingRightPx: Annotated[float, Field(ge=0, le=4096)] = 0
    paddingBottomPx: Annotated[float, Field(ge=0, le=4096)] = 0
    paddingLeftPx: Annotated[float, Field(ge=0, le=4096)] = 0
    alignItems: str = "normal"
    justifyContent: str = "normal"
    gridTemplateColumns: str = "none"
    gridColumn: str = "auto"
    gridRow: str = "auto"
    columnRatio: tuple[float, ...] = ()
    gridPlacement: GridPlacement | None = None
    lineHeight: str = "normal"
    whiteSpace: str = "normal"
    overflow: str = "visible"
    overflowWrap: str = "normal"
    textOverflow: str = "clip"
    objectFit: str = "fill"
    objectPosition: str = "50% 50%"
    textFitPolicy: Literal["none", "shrink", "wrap", "truncate"] = "wrap"
    cropFocalPoint: FocalPoint = Field(default_factory=lambda: FocalPoint(x=0.5, y=0.5))


class ImportedHtmlSource(StrictModel):
    """Sanitized authored source retained as a deterministic renderer asset."""

    html: Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
    htmlHash: Digest
    viewportWidth: Annotated[float, Field(gt=0, le=16384)]
    viewportHeight: Annotated[float, Field(gt=0, le=16384)]
    bindings: Annotated[tuple[ImportedHtmlBinding, ...], Field(min_length=1, max_length=512)]
    importerVersion: Literal[
        "authored-html-import/1",
        "authored-html-import/2",
        "authored-html-import/3",
        "authored-html-import/4",
    ] = "authored-html-import/4"

class RenderRequest(StrictModel):
    spec: SlideSpec
    renderer: RendererManifest
    fonts: FontManifest
    assets: AssetManifest
    source: ImportedHtmlSource | None = None

class SemanticDomEntry(StrictModel):
    semanticId: SemanticId
    layoutId: LayoutId
    qid: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    ]
    tag: Literal["div"] = "div"
    bounds: Bounds | None = None

class RenderResult(StrictModel):
    html: str
    renderInputHash: Digest
    htmlHash: Digest
    semanticDom: tuple[SemanticDomEntry, ...]
    semanticDomHash: Digest
    artifactHashes: dict[str, Digest]
