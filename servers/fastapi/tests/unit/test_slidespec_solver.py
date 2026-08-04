import random
import time

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    AlignmentConstraint,
    AnchorConstraint,
    Bounds,
    ContentNode,
    GapConstraint,
    GroupConstraint,
    LayoutConstraints,
    LayoutNode,
    NonOverlapConstraint,
    SlideDigests,
    SlideSpec,
)
from utils.slidespec_constraints import constraint_structure_error, evaluate_constraints
from utils.slidespec_core import ZERO_DIGEST, apply_repair_plan, seal_slidespec
from utils.slidespec_solver import build_constraint_repair_plan, solve_constraints


def _spec(*, constraints=(), locked_b: bool = False) -> SlideSpec:
    draft = SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId="solver-slide",
        content=(
            ContentNode(semanticId="sem:a", kind="text", text="A", readingOrder=0),
            ContentNode(semanticId="sem:b", kind="text", text="B", readingOrder=1),
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
                    constraints=LayoutConstraints(
                        allowedOperations=("setBounds",),
                        maxTranslation=0.5,
                        maxResizeDelta=0.5,
                    ),
                ),
                LayoutNode(
                    layoutId="lay:b",
                    kind="content",
                    contentSemanticId="sem:b",
                    bounds=Bounds(x=0.35, y=0.15, width=0.2, height=0.1),
                    constraints=LayoutConstraints(
                        locked=locked_b,
                        allowedOperations=("setBounds",),
                        maxTranslation=0.5,
                        maxResizeDelta=0.5,
                    ),
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
    return seal_slidespec(draft)


def test_mixed_anchor_alignment_gap_solution_builds_valid_minimal_plan() -> None:
    spec = _spec(
        constraints=(
            AnchorConstraint(
                constraintId="con:anchor-a",
                kind="anchor",
                target="lay:a",
                container="lay:root",
                targetEdge="left",
                containerEdge="left",
                offset=0.1,
                tolerance=0.001,
            ),
            AlignmentConstraint(
                constraintId="con:tops",
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
                minimum=0.15,
                maximum=0.15,
                tolerance=0.001,
            ),
        )
    )

    first = solve_constraints(spec)
    second = solve_constraints(spec)
    plan = build_constraint_repair_plan(spec)

    assert first.feasible is True
    assert first == second
    assert plan is not None
    assert plan.provenance.policy_version == "mixed-constraint-solver/1.0"
    repaired = apply_repair_plan(spec, plan).spec
    assert evaluate_constraints(repaired).hard_failure is False
    assert len(plan.operations) <= 2


def test_mixed_group_anchor_conflict_returns_inclusion_minimal_unsat_core() -> None:
    spec = _spec(
        constraints=(
            GroupConstraint(
                constraintId="con:group",
                kind="group",
                members=("lay:a", "lay:b"),
            ),
            AnchorConstraint(
                constraintId="con:anchor-a",
                kind="anchor",
                target="lay:a",
                container="lay:root",
                targetEdge="left",
                containerEdge="left",
                offset=0.1,
                tolerance=0.0001,
            ),
            AnchorConstraint(
                constraintId="con:anchor-b",
                kind="anchor",
                target="lay:b",
                container="lay:root",
                targetEdge="left",
                containerEdge="left",
                offset=0.6,
                tolerance=0.0001,
            ),
        )
    )

    result = solve_constraints(spec)

    assert result.feasible is False
    assert result.unsat_core == ("con:anchor-a", "con:anchor-b", "con:group")
    assert result.unsat_core_kind == "cardinality-minimum"
    assert result.core_search_complete is True
    assert result.reason_codes == (
        "constraint.feasibility.unsatisfied",
        "constraint.unsat_core.cardinality_minimum",
    )
    for removed in result.unsat_core:
        reduced = spec.model_copy(
            update={
                "constraints": tuple(
                    constraint
                    for constraint in spec.constraints
                    if constraint.constraintId != removed
                )
            }
        )
        assert solve_constraints(reduced, compute_unsat_core=False).feasible is True


def test_cardinality_core_beats_deletion_order_and_has_stable_tie_break(monkeypatch) -> None:
    """A size-2 conflict wins even when deletion first discovers a size-3 MUS."""

    import utils.slidespec_solver as solver_module

    constraints = tuple(
        AnchorConstraint(
            constraintId=constraint_id,
            kind="anchor",
            target="lay:a",
            container="lay:root",
            targetEdge="left",
            containerEdge="left",
            offset=0.1,
            tolerance=0.001,
        )
        for constraint_id in (
            "con:a-pair-1",
            "con:a-pair-2",
            "con:z-triple-1",
            "con:z-triple-2",
            "con:z-triple-3",
        )
    )
    spec = _spec(constraints=constraints)
    pair = frozenset(("con:a-pair-1", "con:a-pair-2"))
    triple = frozenset(("con:z-triple-1", "con:z-triple-2", "con:z-triple-3"))

    def fake_solve_active(_spec, active_ids):
        unsat = pair <= active_ids or triple <= active_ids
        return solver_module.FeasibilityResult(
            feasible=not unsat,
            bounds={} if unsat else {"lay:root": solver_module.SolvedRect(0, 0, 1, 1)},
        )

    monkeypatch.setattr(solver_module, "_solve_active", fake_solve_active)

    first = solver_module.solve_constraints(spec)
    second = solver_module.solve_constraints(spec)

    assert first == second
    assert first.unsat_core == ("con:a-pair-1", "con:a-pair-2")
    assert first.unsat_core_kind == "cardinality-minimum"
    assert first.core_search_complete is True
    assert first.core_oracle_calls > len(constraints)


def test_cardinality_budget_returns_proved_inclusion_minimal_fallback(monkeypatch) -> None:
    import utils.slidespec_solver as solver_module

    constraints = tuple(
        AnchorConstraint(
            constraintId=constraint_id,
            kind="anchor",
            target="lay:a",
            container="lay:root",
            targetEdge="left",
            containerEdge="left",
            offset=0.1,
            tolerance=0.001,
        )
        for constraint_id in (
            "con:a-pair-1",
            "con:a-pair-2",
            "con:z-triple-1",
            "con:z-triple-2",
            "con:z-triple-3",
        )
    )
    spec = _spec(constraints=constraints)
    pair = frozenset(("con:a-pair-1", "con:a-pair-2"))
    triple = frozenset(("con:z-triple-1", "con:z-triple-2", "con:z-triple-3"))

    def fake_solve_active(_spec, active_ids):
        return solver_module.FeasibilityResult(
            feasible=not (pair <= active_ids or triple <= active_ids),
            bounds={},
        )

    monkeypatch.setattr(solver_module, "_solve_active", fake_solve_active)

    result = solver_module.solve_constraints(spec, cardinality_core_budget=0)

    assert result.unsat_core == (
        "con:z-triple-1",
        "con:z-triple-2",
        "con:z-triple-3",
    )
    assert result.unsat_core_kind == "inclusion-minimal-fallback"
    assert result.core_search_complete is False
    assert result.reason_codes == (
        "constraint.feasibility.unsatisfied",
        "constraint.unsat_core.inclusion_minimal_fallback",
    )


def test_negative_cardinality_core_budget_is_rejected() -> None:
    try:
        solve_constraints(_spec(), cardinality_core_budget=-1)
    except ValueError as error:
        assert str(error) == "cardinality_core_budget must be non-negative"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("negative cardinality core budget must fail")


def test_mutation_envelope_participates_in_feasibility() -> None:
    spec = _spec(
        locked_b=True,
        constraints=(
            GapConstraint(
                constraintId="con:locked-gap",
                kind="gap",
                leading="lay:a",
                trailing="lay:b",
                axis="horizontal",
                minimum=0.2502,
                maximum=0.2502,
                tolerance=0.0001,
            ),
            AnchorConstraint(
                constraintId="con:fixed-a",
                kind="anchor",
                target="lay:a",
                container="lay:root",
                targetEdge="left",
                containerEdge="left",
                offset=0.1,
                tolerance=0.0001,
            ),
        ),
    )

    result = solve_constraints(spec)

    assert result.feasible is False
    assert set(result.unsat_core) == {
        "con:locked-gap",
        "con:fixed-a",
        "mutation-envelope:lay:b",
    }


def test_non_overlap_branch_is_deterministic_and_minimum_movement() -> None:
    spec = _spec(
        constraints=(
            NonOverlapConstraint(
                constraintId="con:no-overlap",
                kind="nonOverlap",
                targets=("lay:a", "lay:b"),
                clearance=0.1,
                tolerance=0.001,
            ),
        )
    )

    result = solve_constraints(spec)

    assert result.feasible is True
    assert result.branches_evaluated == 4
    assert solve_constraints(spec).bounds == result.bounds
    left = result.bounds["lay:a"]
    right = result.bounds["lay:b"]
    separated = (
        left.right + 0.099 <= right.x
        or right.right + 0.099 <= left.x
        or left.bottom + 0.099 <= right.y
        or right.bottom + 0.099 <= left.y
    )
    assert separated


def test_bounded_sat_witness_is_not_mislabeled_unknown_or_minimum(monkeypatch) -> None:
    import utils.slidespec_solver as solver_module

    spec = _spec(
        constraints=(
            NonOverlapConstraint(
                constraintId="con:no-overlap-bounded",
                kind="nonOverlap",
                targets=("lay:a", "lay:b"),
                clearance=0.1,
                tolerance=0.001,
            ),
        )
    )
    monkeypatch.setattr(solver_module, "_MAX_BRANCHES", 1)

    result = solver_module.solve_constraints(spec)

    assert result.feasible is True
    assert result.search_complete is True
    assert result.optimality_proven is False
    assert result.reason_codes == ("constraint.solver.optimality_bounded",)
    assert result.branches_evaluated == 1
    assert solver_module.build_constraint_repair_plan(spec) is None


def test_seeded_mixed_constraint_property_is_deterministic_and_hard_valid() -> None:
    started = time.perf_counter()
    generator = random.Random(20260804)

    for case in range(24):
        gap = round(generator.uniform(0.03, 0.28), 6)
        spec = _spec(
            constraints=(
                AnchorConstraint(
                    constraintId=f"con:anchor-{case}",
                    kind="anchor",
                    target="lay:a",
                    container="lay:root",
                    targetEdge="left",
                    containerEdge="left",
                    offset=0.1,
                    tolerance=0.0005,
                ),
                AlignmentConstraint(
                    constraintId=f"con:align-{case}",
                    kind="alignment",
                    targets=("lay:a", "lay:b"),
                    edge="top",
                    tolerance=0.0005,
                ),
                GapConstraint(
                    constraintId=f"con:gap-{case}",
                    kind="gap",
                    leading="lay:a",
                    trailing="lay:b",
                    axis="horizontal",
                    minimum=gap,
                    maximum=gap,
                    tolerance=0.0005,
                ),
            )
        )

        first = solve_constraints(spec)
        second = solve_constraints(spec)
        plan = build_constraint_repair_plan(spec)

        assert first == second
        assert first.feasible is True
        assert first.search_complete is True
        assert first.metrics()["variable_count"] == 12
        assert plan is not None
        repaired = apply_repair_plan(spec, plan).spec
        assert evaluate_constraints(repaired).hard_failure is False

    # A loose ceiling catches accidental exponential regressions without making
    # ordinary shared Windows CI load a source of flaky failures.
    assert time.perf_counter() - started < 5.0


def test_bounded_non_overlap_search_is_unknown_not_false_unsat(monkeypatch) -> None:
    import utils.slidespec_solver as solver_module

    children = tuple(
        LayoutNode(
            layoutId=f"lay:{name}",
            kind="content",
            contentSemanticId=f"sem:{name}",
            bounds=Bounds(x=0.1, y=0.1, width=0.3, height=0.2),
            constraints=LayoutConstraints(locked=True, allowedOperations=()),
        )
        for name in ("a", "b", "c", "d")
    )
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId="solver-search-limit",
            content=tuple(
                ContentNode(
                    semanticId=f"sem:{name}",
                    kind="text",
                    text=name,
                    readingOrder=index,
                )
                for index, name in enumerate(("a", "b", "c", "d"))
            ),
            layout=LayoutNode(layoutId="lay:root", kind="canvas", children=children),
            constraints=(
                NonOverlapConstraint(
                    constraintId="con:all-pairs",
                    kind="nonOverlap",
                    targets=tuple(child.layoutId for child in children),
                ),
            ),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )
    monkeypatch.setattr(solver_module, "_MAX_BRANCHES", 2)

    result = solve_constraints(spec)

    assert result.feasible is False
    assert result.search_complete is False
    assert result.unsat_core == ()
    assert result.reason_codes == ("constraint.feasibility.search_limited",)
    assert result.branches_evaluated == 2
    assert build_constraint_repair_plan(spec) is None
    assert constraint_structure_error(spec) == (
        "spec.constraint.feasibility_unknown",
        "constraint.feasibility.search_limited",
    )


def test_large_feasible_non_overlap_set_short_circuits_at_global_minimum() -> None:
    positions = ((0.05, 0.05), (0.55, 0.05), (0.05, 0.55), (0.55, 0.55))
    children = tuple(
        LayoutNode(
            layoutId=f"lay:{index}",
            kind="content",
            contentSemanticId=f"sem:{index}",
            bounds=Bounds(x=x, y=y, width=0.25, height=0.25),
            constraints=LayoutConstraints(locked=True, allowedOperations=()),
        )
        for index, (x, y) in enumerate(positions)
    )
    spec = seal_slidespec(
        SlideSpec(
            schemaUri=SLIDESPEC_SCHEMA,
            specRevision=1,
            slideId="solver-large-feasible-non-overlap",
            content=tuple(
                ContentNode(
                    semanticId=f"sem:{index}",
                    kind="text",
                    text=str(index),
                    readingOrder=index,
                )
                for index in range(4)
            ),
            layout=LayoutNode(layoutId="lay:root", kind="canvas", children=children),
            constraints=(
                NonOverlapConstraint(
                    constraintId="con:all-feasible-pairs",
                    kind="nonOverlap",
                    targets=tuple(child.layoutId for child in children),
                ),
            ),
            digests=SlideDigests(
                content=ZERO_DIGEST,
                hierarchy=ZERO_DIGEST,
                importantTokens=ZERO_DIGEST,
                spec=ZERO_DIGEST,
            ),
        )
    )

    started = time.perf_counter()
    result = solve_constraints(spec)

    assert result.feasible is True
    assert result.search_complete is True
    assert result.reason_codes == ()
    assert result.non_overlap_pairs == 6
    assert result.branches_evaluated == 1
    assert result.max_residual == 0
    assert time.perf_counter() - started < 0.25
