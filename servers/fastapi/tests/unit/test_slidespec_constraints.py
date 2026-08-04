from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AlignmentConstraint,
    AnchorConstraint,
    Bounds,
    ContainmentConstraint,
    ContentNode,
    GapConstraint,
    GroupConstraint,
    LayoutNode,
    NonOverlapConstraint,
    OrderConstraint,
    ReadabilityConstraint,
    SlideDigests,
    SlideSpec,
)
from utils.slidespec_constraints import (
    LayoutObservation,
    Rect,
    constraint_structure_error,
    evaluate_constraints,
    evaluate_repair_transition,
    has_soft_constraint_regression,
)
from utils.slidespec_core import ZERO_DIGEST, seal_slidespec


def _draft(*, constraints=()) -> SlideSpec:
    return SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId="slide-relations",
        content=(
            ContentNode(semanticId="sem:a", kind="title", text="A", readingOrder=0),
            ContentNode(semanticId="sem:b", kind="text", text="B", readingOrder=1),
            ContentNode(semanticId="sem:c", kind="text", text="C", readingOrder=2),
        ),
        layout=LayoutNode(
            layoutId="lay:root",
            kind="canvas",
            children=(
                LayoutNode(
                    layoutId="lay:a",
                    kind="content",
                    contentSemanticId="sem:a",
                    bounds=Bounds(x=0.1, y=0.1, width=0.2, height=0.1),
                ),
                LayoutNode(
                    layoutId="lay:b",
                    kind="content",
                    contentSemanticId="sem:b",
                    bounds=Bounds(x=0.4, y=0.1, width=0.2, height=0.1),
                ),
                LayoutNode(
                    layoutId="lay:c",
                    kind="content",
                    contentSemanticId="sem:c",
                    bounds=Bounds(x=0.1, y=0.4, width=0.2, height=0.1),
                ),
            ),
        ),
        constraints=constraints,
        digests=SlideDigests(
            content=ZERO_DIGEST,
            hierarchy=ZERO_DIGEST,
            importantTokens=ZERO_DIGEST,
            spec=ZERO_DIGEST,
        ),
    )


def _constraints():
    return (
        GroupConstraint(
            constraintId="con:group",
            kind="group",
            members=("lay:a", "lay:b"),
        ),
        AnchorConstraint(
            constraintId="con:anchor",
            kind="anchor",
            target="lay:a",
            container="lay:root",
            targetEdge="left",
            containerEdge="left",
            offset=0.1,
            tolerance=0.001,
        ),
        AlignmentConstraint(
            constraintId="con:alignment",
            kind="alignment",
            targets=("lay:a", "lay:b"),
            edge="top",
            tolerance=0.001,
        ),
        GapConstraint(
            constraintId="con:gap",
            kind="gap",
            leading="lay:a",
            trailing="lay:b",
            axis="horizontal",
            minimum=0.09,
            maximum=0.11,
            preferred=0.1,
            tolerance=0.001,
        ),
        ContainmentConstraint(
            constraintId="con:containment",
            kind="containment",
            targets=("lay:a", "lay:b", "lay:c"),
            container="lay:root",
        ),
        NonOverlapConstraint(
            constraintId="con:non-overlap",
            kind="nonOverlap",
            targets=("lay:a", "lay:b"),
        ),
        OrderConstraint(
            constraintId="con:order",
            kind="order",
            before="lay:a",
            after="lay:b",
            orderBy="reading",
        ),
        ReadabilityConstraint(
            constraintId="con:readability",
            kind="readability",
            targets=("lay:a",),
            minFontSizePx=18,
            minLineHeightRatio=1.2,
            minContrastRatio=4.5,
        ),
    )


def _observation(rect: Rect, **updates) -> LayoutObservation:
    values = {
        "bounds": rect,
        "font_size_px": 24,
        "line_height_ratio": 1.3,
        "contrast_ratio": 7,
        "visible": True,
        "truncated": False,
        "z_index": 1,
        "rotation_degrees": 0,
        "occluded_fraction": 0,
        "glyph_clipped": False,
        "line_box_clipped": False,
    }
    values.update(updates)
    return LayoutObservation(**values)


def test_all_eight_constraint_kinds_pass_on_observed_baseline() -> None:
    spec = seal_slidespec(_draft(constraints=_constraints()))
    observations = {
        "lay:root": _observation(Rect(0, 0, 1, 1)),
        "lay:a": _observation(Rect(0.1, 0.1, 0.2, 0.1)),
        "lay:b": _observation(Rect(0.4, 0.1, 0.2, 0.1)),
        "lay:c": _observation(Rect(0.1, 0.4, 0.2, 0.1)),
    }

    report = evaluate_constraints(spec, observations=observations)

    assert {constraint.kind for constraint in spec.constraints} == {
        "group",
        "anchor",
        "alignment",
        "gap",
        "containment",
        "nonOverlap",
        "order",
        "readability",
    }
    assert report.hard_failure is False
    assert report.violations == ()


def test_observed_geometry_and_readability_violations_fail_closed() -> None:
    spec = seal_slidespec(_draft(constraints=_constraints()))
    observations = {
        "lay:root": _observation(Rect(0, 0, 1, 1)),
        "lay:a": _observation(
            Rect(0.2, 0.2, 0.2, 0.1),
            font_size_px=12,
            line_height_ratio=1,
            contrast_ratio=2,
            visible=False,
            truncated=True,
        ),
        "lay:b": _observation(Rect(0.25, 0.15, 0.2, 0.1)),
        "lay:c": _observation(Rect(0.9, 0.9, 0.2, 0.2)),
    }

    report = evaluate_constraints(spec, observations=observations)
    codes = {violation.code for violation in report.violations}

    assert report.hard_failure is True
    assert {
        "constraint.anchor.violated",
        "constraint.alignment.violated",
        "constraint.gap.violated",
        "constraint.containment.violated",
        "constraint.non_overlap.violated",
        "constraint.readability.violated",
    } <= codes


def test_group_atomicity_is_checked_across_repair_transition() -> None:
    base = seal_slidespec(_draft(constraints=_constraints()))
    moved_a = base.layout.children[0].model_copy(
        update={"bounds": Bounds(x=0.15, y=0.1, width=0.2, height=0.1)}
    )
    candidate = seal_slidespec(
        base.model_copy(
            update={
                "specRevision": 2,
                "layout": base.layout.model_copy(
                    update={"children": (moved_a, *base.layout.children[1:])}
                ),
            }
        )
    )

    report = evaluate_repair_transition(base, candidate)

    assert report.hard_failure is True
    assert "constraint.group.atomic_translation" in {
        violation.code for violation in report.violations
    }


def test_group_atomicity_is_rechecked_from_actual_browser_observations() -> None:
    spec = seal_slidespec(_draft(constraints=(_constraints()[0],)))
    baseline = {
        "lay:a": _observation(Rect(0.1, 0.1, 0.2, 0.1)),
        "lay:b": _observation(Rect(0.4, 0.1, 0.2, 0.1)),
    }
    candidate = {
        "lay:a": _observation(Rect(0.15, 0.1, 0.2, 0.1)),
        "lay:b": _observation(Rect(0.4, 0.1, 0.2, 0.1)),
    }

    report = evaluate_constraints(
        spec,
        observations=candidate,
        baseline_observations=baseline,
    )

    assert report.hard_failure is True
    assert report.primary_code == "constraint.group.atomic_translation"


def test_group_scale_and_rotation_must_be_coordinated_from_browser_evidence() -> None:
    spec = seal_slidespec(_draft(constraints=(_constraints()[0],)))
    baseline = {
        "lay:a": _observation(Rect(0.1, 0.1, 0.2, 0.1), rotation_degrees=0),
        "lay:b": _observation(Rect(0.4, 0.1, 0.2, 0.1), rotation_degrees=0),
    }
    candidate = {
        "lay:a": _observation(Rect(0.1, 0.1, 0.24, 0.12), rotation_degrees=12),
        "lay:b": _observation(Rect(0.4, 0.1, 0.22, 0.11), rotation_degrees=4),
    }

    report = evaluate_constraints(
        spec,
        observations=candidate,
        baseline_observations=baseline,
    )
    codes = {violation.code for violation in report.violations}

    assert report.hard_failure is True
    assert "constraint.group.coordinated_scale" in codes
    assert "constraint.group.coordinated_rotation" in codes


def test_transformed_quads_drive_containment_and_overlap_instead_of_aabb() -> None:
    constraints = (
        ContainmentConstraint(
            constraintId="con:quad-containment",
            kind="containment",
            targets=("lay:a",),
            container="lay:root",
        ),
        NonOverlapConstraint(
            constraintId="con:quad-overlap",
            kind="nonOverlap",
            targets=("lay:a", "lay:b"),
        ),
    )
    spec = seal_slidespec(_draft(constraints=constraints))
    observations = {
        "lay:root": _observation(
            Rect(0, 0, 1, 1),
            quad=((0, 0), (1, 0), (1, 1), (0, 1)),
        ),
        "lay:a": _observation(
            Rect(0.1, 0.1, 0.2, 0.1),
            quad=((0.1, 0.1), (1.05, 0.1), (0.45, 0.2), (0.1, 0.2)),
        ),
        "lay:b": _observation(
            Rect(0.4, 0.1, 0.2, 0.1),
            quad=((0.4, 0.1), (0.6, 0.1), (0.6, 0.2), (0.4, 0.2)),
        ),
    }

    report = evaluate_constraints(spec, observations=observations)
    codes = {violation.code for violation in report.violations}

    assert "constraint.containment.violated" in codes
    assert "constraint.non_overlap.violated" in codes


def test_readability_uses_occlusion_and_glyph_line_clipping_fail_closed() -> None:
    readability = ReadabilityConstraint(
        constraintId="con:measured-readability",
        kind="readability",
        targets=("lay:a",),
        requireVisible=True,
        noTruncation=True,
        minContrastRatio=4.5,
    )
    spec = seal_slidespec(_draft(constraints=(readability,)))
    bad = evaluate_constraints(
        spec,
        observations={
            "lay:a": _observation(
                Rect(0.1, 0.1, 0.2, 0.1),
                contrast_ratio=2.5,
                occluded_fraction=0.4,
                glyph_clipped=True,
                line_box_clipped=True,
            )
        },
    )
    missing = evaluate_constraints(
        spec,
        observations={
            "lay:a": _observation(
                Rect(0.1, 0.1, 0.2, 0.1),
                contrast_ratio=None,
                occluded_fraction=None,
                glyph_clipped=None,
                line_box_clipped=None,
            )
        },
    )

    assert bad.hard_failure is True
    assert {
        "lay:a:contrast",
        "lay:a:occlusion",
        "lay:a:truncation",
    } <= {violation.detail for violation in bad.violations}
    assert missing.hard_failure is True
    assert missing.primary_code == "constraint.evidence.missing"
    assert missing.unknown_hard == ("con:measured-readability",)


def test_no_truncation_rejects_truncate_policy_before_render() -> None:
    readability = ReadabilityConstraint(
        constraintId="con:no-truncate",
        kind="readability",
        targets=("lay:a",),
        noTruncation=True,
        requireVisible=False,
    )
    draft = _draft(constraints=(readability,))
    truncated = draft.layout.children[0].model_copy(
        update={"textFitPolicy": "truncate"}
    )
    spec = seal_slidespec(
        draft.model_copy(
            update={
                "layout": draft.layout.model_copy(
                    update={"children": (truncated, *draft.layout.children[1:])}
                )
            }
        )
    )

    report = evaluate_constraints(spec)

    assert report.hard_failure is True
    assert report.primary_code == "constraint.readability.violated"


def test_structural_conflicts_are_rejected_before_render() -> None:
    constraints = (
        AnchorConstraint(
            constraintId="con:left-1",
            kind="anchor",
            target="lay:a",
            container="lay:root",
            targetEdge="left",
            containerEdge="left",
            offset=0.1,
            tolerance=0.001,
        ),
        AnchorConstraint(
            constraintId="con:left-2",
            kind="anchor",
            target="lay:a",
            container="lay:root",
            targetEdge="left",
            containerEdge="left",
            offset=0.2,
            tolerance=0.001,
        ),
    )

    error = constraint_structure_error(_draft(constraints=constraints))

    assert error == ("spec.constraint.hard_conflict", "anchor")


def test_weighted_soft_constraint_cost_cannot_regress() -> None:
    soft_gap = GapConstraint(
        constraintId="con:soft-gap",
        kind="gap",
        leading="lay:a",
        trailing="lay:b",
        axis="horizontal",
        preferred=0.1,
        maximum=0.5,
        strength="soft",
        priority=80,
        confidence=0.75,
        tolerance=0.001,
    )
    spec = seal_slidespec(_draft(constraints=(soft_gap,)))
    baseline = evaluate_constraints(
        spec,
        observations={
            "lay:a": _observation(Rect(0.1, 0.1, 0.2, 0.1)),
            "lay:b": _observation(Rect(0.4, 0.1, 0.2, 0.1)),
        },
    )
    candidate = evaluate_constraints(
        spec,
        observations={
            "lay:a": _observation(Rect(0.1, 0.1, 0.2, 0.1)),
            "lay:b": _observation(Rect(0.5, 0.1, 0.2, 0.1)),
        },
    )

    assert baseline.soft_cost == 0
    assert candidate.soft_cost > 0
    assert has_soft_constraint_regression(baseline, candidate) is True
