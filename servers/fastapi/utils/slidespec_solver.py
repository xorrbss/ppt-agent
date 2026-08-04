"""Deterministic mixed-constraint feasibility and minimum-change repair.

The solver intentionally stays dependency-free.  It represents geometry as
linear half-spaces, uses deterministic cyclic projections for each convex
non-overlap branch, and derives a cardinality-minimum unsatisfiable core when
its bounded deterministic search completes.  If that secondary search reaches
its resource limit, the solver returns the already-proved inclusion-minimal
core and labels the fallback explicitly.  The same feasible point is converted
into a bounded RepairPlan so validation and repair cannot disagree about the
constraint model.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import combinations, product
import hashlib
import json
from math import isfinite
from typing import Iterable, Mapping, Sequence

from models.slidespec_v1 import (
    REPAIR_PLAN_SCHEMA,
    AlignmentConstraint,
    AnchorConstraint,
    Bounds,
    ContainmentConstraint,
    GapConstraint,
    GroupConstraint,
    LayoutNode,
    NonOverlapConstraint,
    OrderConstraint,
    RepairPlan,
    SlideConstraint,
    SlideSpec,
)


SOLVER_POLICY_VERSION = "mixed-constraint-solver/1.0"
_EPSILON = 1e-7
_MAX_ITERATIONS = 2048
_MAX_EXHAUSTIVE_NON_OVERLAP_PAIRS = 5
_MAX_BRANCHES = 1024
_MAX_CARDINALITY_CORE_CHECKS = 4096


@dataclass(frozen=True, slots=True)
class SolvedRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class FeasibilityResult:
    feasible: bool
    bounds: Mapping[str, SolvedRect]
    unsat_core: tuple[str, ...] = ()
    iterations: int = 0
    branches_evaluated: int = 0
    max_residual: float = 0
    reason_codes: tuple[str, ...] = ()
    search_complete: bool = True
    variable_count: int = 0
    half_space_count: int = 0
    non_overlap_pairs: int = 0
    unsat_core_kind: str = "none"
    core_oracle_calls: int = 0
    core_search_complete: bool = True
    optimality_proven: bool = True

    def metrics(self) -> dict[str, int | float | bool]:
        """Return stable, low-cardinality solver observability fields."""

        return {
            "feasible": self.feasible,
            "search_complete": self.search_complete,
            "iterations": self.iterations,
            "branches_evaluated": self.branches_evaluated,
            "variable_count": self.variable_count,
            "half_space_count": self.half_space_count,
            "non_overlap_pairs": self.non_overlap_pairs,
            "max_residual": round(self.max_residual, 9),
            "unsat_core_size": len(self.unsat_core),
            "unsat_core_kind": self.unsat_core_kind,
            "core_oracle_calls": self.core_oracle_calls,
            "core_search_complete": self.core_search_complete,
            "optimality_proven": self.optimality_proven,
        }


@dataclass(frozen=True, slots=True)
class _HalfSpace:
    coefficients: tuple[tuple[int, float], ...]
    upper: float
    owner: str

    def residual(self, values: Sequence[float]) -> float:
        return sum(values[index] * coefficient for index, coefficient in self.coefficients) - self.upper


def _walk(root: LayoutNode) -> tuple[LayoutNode, ...]:
    result: list[LayoutNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return tuple(result)


def _parents(root: LayoutNode) -> dict[str, str | None]:
    result: dict[str, str | None] = {root.layoutId: None}
    stack = [root]
    while stack:
        node = stack.pop()
        for child in node.children:
            result[child.layoutId] = node.layoutId
            stack.append(child)
    return result


def _absolute_bounds(spec: SlideSpec) -> dict[str, SolvedRect]:
    result: dict[str, SolvedRect] = {}

    def visit(node: LayoutNode, parent: SolvedRect, root: bool = False) -> None:
        if root and node.bounds is None:
            current = SolvedRect(0, 0, 1, 1)
        elif node.bounds is None:
            current = parent
        else:
            current = SolvedRect(
                parent.x + node.bounds.x * parent.width,
                parent.y + node.bounds.y * parent.height,
                node.bounds.width * parent.width,
                node.bounds.height * parent.height,
            )
        result[node.layoutId] = current
        for child in node.children:
            visit(child, current)

    visit(spec.layout, SolvedRect(0, 0, 1, 1), True)
    return result


class _Problem:
    def __init__(self, spec: SlideSpec, active_ids: frozenset[str]) -> None:
        self.spec = spec
        self.nodes = {node.layoutId: node for node in _walk(spec.layout)}
        self.node_ids = tuple(sorted(self.nodes))
        self.offset = {layout_id: index * 4 for index, layout_id in enumerate(self.node_ids)}
        self.base = _absolute_bounds(spec)
        self.initial = [0.0] * (4 * len(self.node_ids))
        for layout_id in self.node_ids:
            rect = self.base[layout_id]
            start = self.offset[layout_id]
            self.initial[start : start + 4] = [rect.x, rect.y, rect.width, rect.height]
        self.spaces: list[_HalfSpace] = []
        self.non_overlap: list[tuple[str, str, float, str]] = []
        self._add_envelope(active_ids)
        self._add_relational(active_ids)

    def variable(self, layout_id: str, component: str) -> int:
        return self.offset[layout_id] + {"x": 0, "y": 1, "w": 2, "h": 3}[component]

    def expression(self, layout_id: str, edge: str) -> dict[int, float]:
        x = self.variable(layout_id, "x")
        y = self.variable(layout_id, "y")
        w = self.variable(layout_id, "w")
        h = self.variable(layout_id, "h")
        return {
            "left": {x: 1},
            "centerX": {x: 1, w: 0.5},
            "right": {x: 1, w: 1},
            "top": {y: 1},
            "centerY": {y: 1, h: 0.5},
            "bottom": {y: 1, h: 1},
        }[edge]

    @staticmethod
    def _combine(*items: object) -> dict[int, float]:
        if len(items) % 2:
            raise ValueError("linear expression requires expression/scale pairs")
        result: dict[int, float] = {}
        for offset in range(0, len(items), 2):
            expression = items[offset]
            scale = items[offset + 1]
            if not isinstance(expression, Mapping) or not isinstance(scale, (int, float)):
                raise TypeError("linear expression requires mapping/number pairs")
            for index, coefficient in expression.items():
                result[index] = result.get(index, 0) + coefficient * scale
        return {index: coefficient for index, coefficient in result.items() if coefficient}

    def le(self, expression: Mapping[int, float], upper: float, owner: str) -> None:
        self.spaces.append(_HalfSpace(tuple(sorted(expression.items())), upper, owner))

    def ge(self, expression: Mapping[int, float], lower: float, owner: str) -> None:
        self.le({index: -value for index, value in expression.items()}, -lower, owner)

    def eq(self, expression: Mapping[int, float], value: float, tolerance: float, owner: str) -> None:
        self.le(expression, value + tolerance, owner)
        self.ge(expression, value - tolerance, owner)

    def _add_envelope(self, active_ids: frozenset[str]) -> None:
        parents = _parents(self.spec.layout)
        for layout_id in self.node_ids:
            node = self.nodes[layout_id]
            base = self.base[layout_id]
            owner = f"mutation-envelope:{layout_id}"
            x = {self.variable(layout_id, "x"): 1}
            y = {self.variable(layout_id, "y"): 1}
            w = {self.variable(layout_id, "w"): 1}
            h = {self.variable(layout_id, "h"): 1}
            self.ge(w, _EPSILON, owner)
            self.ge(h, _EPSILON, owner)
            parent_id = parents[layout_id]
            if parent_id is None:
                self.eq(x, 0, 0, owner)
                self.eq(y, 0, 0, owner)
                self.eq(w, 1, 0, owner)
                self.eq(h, 1, 0, owner)
                continue

            parent_left = self.expression(parent_id, "left")
            parent_right = self.expression(parent_id, "right")
            parent_top = self.expression(parent_id, "top")
            parent_bottom = self.expression(parent_id, "bottom")
            self.ge(self._combine(x, 1, parent_left, -1), 0, owner)
            self.le(self._combine(self.expression(layout_id, "right"), 1, parent_right, -1), 0, owner)
            self.ge(self._combine(y, 1, parent_top, -1), 0, owner)
            self.le(self._combine(self.expression(layout_id, "bottom"), 1, parent_bottom, -1), 0, owner)

            envelope = node.constraints
            if envelope is None or owner not in active_ids:
                continue
            parent_base = self.base[parent_id]
            self.ge(w, envelope.minWidth * parent_base.width, owner)
            self.le(w, envelope.maxWidth * parent_base.width, owner)
            self.ge(h, envelope.minHeight * parent_base.height, owner)
            self.le(h, envelope.maxHeight * parent_base.height, owner)
            allowed = set(envelope.allowedOperations or ())
            unrestricted = envelope.allowedOperations is None
            position_mutable = unrestricted or bool(allowed & {"setBounds", "translateWithinParent"})
            size_mutable = unrestricted or bool(allowed & {"setBounds", "resizeWithinBounds"})
            if envelope.locked or envelope.lockPosition or not position_mutable:
                self.eq(x, base.x, 0, owner)
                self.eq(y, base.y, 0, owner)
            else:
                self.ge(x, base.x - envelope.maxTranslation, owner)
                self.le(x, base.x + envelope.maxTranslation, owner)
                self.ge(y, base.y - envelope.maxTranslation, owner)
                self.le(y, base.y + envelope.maxTranslation, owner)
            if envelope.locked or envelope.lockSize or not size_mutable:
                self.eq(w, base.width, 0, owner)
                self.eq(h, base.height, 0, owner)
            else:
                self.ge(w, max(_EPSILON, base.width - envelope.maxResizeDelta), owner)
                self.le(w, base.width + envelope.maxResizeDelta, owner)
                self.ge(h, max(_EPSILON, base.height - envelope.maxResizeDelta), owner)
                self.le(h, base.height + envelope.maxResizeDelta, owner)
            if envelope.preserveAspectRatio:
                self.eq(
                    {
                        self.variable(layout_id, "w"): base.height,
                        self.variable(layout_id, "h"): -base.width,
                    },
                    0,
                    2e-7,
                    owner,
                )

    def _add_relational(self, active_ids: frozenset[str]) -> None:
        for constraint in sorted(
            self.spec.constraints, key=lambda item: item.constraintId
        ):
            if constraint.strength != "hard" or constraint.constraintId not in active_ids:
                continue
            owner = constraint.constraintId
            if isinstance(constraint, AnchorConstraint):
                expression = self._combine(
                    self.expression(constraint.target, constraint.targetEdge), 1,
                    self.expression(constraint.container, constraint.containerEdge), -1,
                )
                self.eq(expression, constraint.offset, constraint.tolerance * 0.5, owner)
            elif isinstance(constraint, AlignmentConstraint):
                reference = constraint.targets[0]
                for target in constraint.targets[1:]:
                    expression = self._combine(
                        self.expression(target, constraint.edge), 1,
                        self.expression(reference, constraint.edge), -1,
                    )
                    self.eq(expression, 0, constraint.tolerance * 0.5, owner)
            elif isinstance(constraint, GapConstraint):
                leading = self.expression(constraint.leading, "right" if constraint.axis == "horizontal" else "bottom")
                trailing = self.expression(constraint.trailing, "left" if constraint.axis == "horizontal" else "top")
                expression = self._combine(trailing, 1, leading, -1)
                self.ge(expression, constraint.minimum - constraint.tolerance * 0.5, owner)
                if constraint.maximum is not None:
                    self.le(expression, constraint.maximum + constraint.tolerance * 0.5, owner)
            elif isinstance(constraint, ContainmentConstraint):
                container_x = self.expression(constraint.container, "left")
                container_y = self.expression(constraint.container, "top")
                container_w = {self.variable(constraint.container, "w"): 1}
                container_h = {self.variable(constraint.container, "h"): 1}
                for target in constraint.targets:
                    tolerance = constraint.tolerance * 0.5
                    self.ge(self._combine(self.expression(target, "left"), 1, container_x, -1, container_w, -constraint.insets.left), -tolerance, owner)
                    self.ge(self._combine(self.expression(target, "top"), 1, container_y, -1, container_h, -constraint.insets.top), -tolerance, owner)
                    self.le(self._combine(self.expression(target, "right"), 1, container_x, -1, container_w, -(1 - constraint.insets.right)), tolerance, owner)
                    self.le(self._combine(self.expression(target, "bottom"), 1, container_y, -1, container_h, -(1 - constraint.insets.bottom)), tolerance, owner)
            elif isinstance(constraint, NonOverlapConstraint):
                for index, left in enumerate(constraint.targets):
                    for right in constraint.targets[index + 1 :]:
                        self.non_overlap.append((left, right, constraint.clearance - constraint.tolerance * 0.5, owner))
            elif isinstance(constraint, OrderConstraint) and constraint.orderBy in {"x", "y"}:
                before = self.expression(constraint.before, "right" if constraint.orderBy == "x" else "bottom")
                after = self.expression(constraint.after, "left" if constraint.orderBy == "x" else "top")
                self.ge(self._combine(after, 1, before, -1), constraint.minimumSeparation - constraint.tolerance * 0.5, owner)
            elif isinstance(constraint, GroupConstraint):
                reference = constraint.members[0]
                reference_base = self.base[reference]
                for member in constraint.members[1:]:
                    member_base = self.base[member]
                    if constraint.atomicTranslation:
                        self.eq(
                            {
                                self.variable(member, "x"): 1,
                                self.variable(reference, "x"): -1,
                            },
                            member_base.x - reference_base.x,
                            0.002,
                            owner,
                        )
                        self.eq(
                            {
                                self.variable(member, "y"): 1,
                                self.variable(reference, "y"): -1,
                            },
                            member_base.y - reference_base.y,
                            0.002,
                            owner,
                        )
                    # Coordinated resize is represented as equal relative scale.
                    self.eq(
                        {
                            self.variable(member, "w"): reference_base.width,
                            self.variable(reference, "w"): -member_base.width,
                        },
                        0,
                        2e-6,
                        owner,
                    )
                    self.eq(
                        {
                            self.variable(member, "h"): reference_base.height,
                            self.variable(reference, "h"): -member_base.height,
                        },
                        0,
                        2e-6,
                        owner,
                    )

    def non_overlap_branches(self) -> Iterable[tuple[_HalfSpace, ...]]:
        if not self.non_overlap:
            yield ()
            return
        choices: list[tuple[_HalfSpace, ...]] = []
        for left, right, clearance, owner in self.non_overlap:
            options = (
                _HalfSpace(tuple(sorted(self._combine(self.expression(left, "right"), 1, self.expression(right, "left"), -1).items())), -clearance, owner),
                _HalfSpace(tuple(sorted(self._combine(self.expression(right, "right"), 1, self.expression(left, "left"), -1).items())), -clearance, owner),
                _HalfSpace(tuple(sorted(self._combine(self.expression(left, "bottom"), 1, self.expression(right, "top"), -1).items())), -clearance, owner),
                _HalfSpace(tuple(sorted(self._combine(self.expression(right, "bottom"), 1, self.expression(left, "top"), -1).items())), -clearance, owner),
            )
            # Prefer a separation direction already satisfied by the authored
            # geometry.  Absolute edge distance alone cannot distinguish an
            # overlapping axis from a separated axis when their magnitudes tie,
            # which previously put an invalid direction first and triggered up
            # to 1024 expensive projections for an already-valid layout.
            choices.append(
                tuple(
                    sorted(
                        options,
                        key=lambda option: (
                            max(0.0, option.residual(self.initial)),
                            abs(option.residual(self.initial)),
                            option.coefficients,
                        ),
                    )
                )
            )
        if len(choices) <= _MAX_EXHAUSTIVE_NON_OVERLAP_PAIRS:
            yield from product(*choices)
        else:
            # Enumerate the Cartesian product by increasing authored-geometry
            # violation instead of lexicographic product order.  Lexicographic
            # enumeration can spend the entire branch budget changing only the
            # last few pairs; this best-first lattice reaches low-displacement
            # mixed solutions early while preserving deterministic tie breaks.
            penalties = tuple(
                tuple(max(0.0, option.residual(self.initial)) ** 2 for option in options)
                for options in choices
            )
            origin = (0,) * len(choices)
            pending: list[tuple[float, tuple[int, ...]]] = [(0.0, origin)]
            visited = {origin}
            emitted = 0
            while pending and emitted < _MAX_BRANCHES:
                _, indices = heappop(pending)
                emitted += 1
                yield tuple(options[index] for options, index in zip(choices, indices, strict=True))
                for dimension in range(len(indices)):
                    if indices[dimension] == 3:
                        continue
                    neighbour = list(indices)
                    neighbour[dimension] += 1
                    neighbour_key = tuple(neighbour)
                    if neighbour_key in visited:
                        continue
                    visited.add(neighbour_key)
                    score = sum(
                        penalties[item_dimension][item_index]
                        for item_dimension, item_index in enumerate(neighbour_key)
                    )
                    heappush(pending, (score, neighbour_key))

    def rects(self, values: Sequence[float]) -> dict[str, SolvedRect]:
        result: dict[str, SolvedRect] = {}
        for layout_id in self.node_ids:
            start = self.offset[layout_id]
            result[layout_id] = SolvedRect(*values[start : start + 4])
        return result


def _project(problem: _Problem, branch: Sequence[_HalfSpace]) -> tuple[bool, list[float], int, float]:
    values = problem.initial.copy()
    spaces = (*problem.spaces, *branch)
    # Norms are invariant across projection iterations.  Precomputing them is
    # material for large mixed/non-overlap cases (thousands of projection
    # sweeps) and avoids repeated Python-level coefficient walks.
    prepared = tuple(
        (space, sum(coefficient * coefficient for _, coefficient in space.coefficients))
        for space in spaces
    )
    iterations = 0
    for iterations in range(1, _MAX_ITERATIONS + 1):
        maximum = 0.0
        for space, norm in prepared:
            residual = space.residual(values)
            maximum = max(maximum, residual)
            if residual <= _EPSILON:
                continue
            if norm <= _EPSILON:
                return False, values, iterations, residual
            scale = residual / norm
            for index, coefficient in space.coefficients:
                values[index] -= scale * coefficient
        if maximum <= _EPSILON:
            break
    residual = max((space.residual(values) for space in spaces), default=0.0)
    finite = all(isfinite(value) for value in values)
    return finite and residual <= 5e-7, values, iterations, max(0.0, residual)


def _solve_active(spec: SlideSpec, active_ids: frozenset[str]) -> FeasibilityResult:
    problem = _Problem(spec, active_ids)
    best: tuple[float, list[float], int, float] | None = None
    branches = 0
    best_residual = float("inf")
    maximum_iterations = 0
    branch_count = 4 ** len(problem.non_overlap)
    enumeration_complete = branch_count <= _MAX_BRANCHES
    for branch in problem.non_overlap_branches():
        branches += 1
        feasible, values, iterations, residual = _project(problem, branch)
        maximum_iterations = max(maximum_iterations, iterations)
        best_residual = min(best_residual, residual)
        if feasible:
            distance = sum((value - original) ** 2 for value, original in zip(values, problem.initial, strict=True))
            if best is None or (distance, tuple(values)) < (best[0], tuple(best[1])):
                best = (distance, values, iterations, residual)
            # Branches are ordered by their displacement from the authored
            # geometry.  A feasible zero-displacement solution is the global
            # minimum of the non-negative objective, so enumerating the rest of
            # an exponential non-overlap product cannot improve the result.
            if distance <= _EPSILON * _EPSILON:
                break
            # When the product exceeds the branch budget, any feasible witness
            # conclusively resolves feasibility but exhaustive minimum-change
            # optimality is impossible.  Continuing through all 1024 branches
            # added large tail latency without strengthening that conclusion.
            if not enumeration_complete:
                break
    if best is not None:
        # Full enumeration is unnecessary when the non-negative movement
        # objective reaches zero: the witness proves feasibility and global
        # minimum change simultaneously.
        optimality_proven = enumeration_complete or best[0] <= _EPSILON * _EPSILON
        reasons = () if optimality_proven else ("constraint.solver.optimality_bounded",)
        return FeasibilityResult(
            True,
            problem.rects(best[1]),
            iterations=best[2],
            branches_evaluated=branches,
            max_residual=best[3],
            reason_codes=reasons,
            # A witness proves SAT even when the minimum-change branch search
            # is bounded.  ``optimality_proven`` carries the separate repair
            # objective guarantee.
            search_complete=True,
            variable_count=len(problem.initial),
            half_space_count=len(problem.spaces),
            non_overlap_pairs=len(problem.non_overlap),
            optimality_proven=optimality_proven,
        )
    reason = (
        "constraint.feasibility.unsatisfied"
        if enumeration_complete
        else "constraint.feasibility.search_limited"
    )
    return FeasibilityResult(
        False,
        {},
        iterations=maximum_iterations,
        branches_evaluated=branches,
        max_residual=0 if best_residual == float("inf") else best_residual,
        reason_codes=(reason,),
        search_complete=enumeration_complete,
        variable_count=len(problem.initial),
        half_space_count=len(problem.spaces),
        non_overlap_pairs=len(problem.non_overlap),
    )


def solve_constraints(
    spec: SlideSpec,
    *,
    compute_unsat_core: bool = True,
    cardinality_core_budget: int = _MAX_CARDINALITY_CORE_CHECKS,
) -> FeasibilityResult:
    """Solve hard geometry and derive a deterministic bounded UNSAT core.

    The primary feasibility search is never reclassified as UNSAT when its
    branch budget is exhausted.  For a proved UNSAT problem, deletion first
    obtains an inclusion-minimal core.  At most ``cardinality_core_budget``
    additional oracle calls then examine subsets in (size, lexicographic)
    order.  Completing a size proves cardinality minimality; exhausting the
    budget returns the proved inclusion-minimal core with
    ``core_search_complete=False``.  A negative budget is rejected.
    """

    if cardinality_core_budget < 0:
        raise ValueError("cardinality_core_budget must be non-negative")

    hard_ids = tuple(sorted(
        constraint.constraintId
        for constraint in spec.constraints
        if constraint.strength == "hard" and not (
            isinstance(constraint, OrderConstraint) and constraint.orderBy in {"reading", "z"}
        )
    ))
    envelope_ids = tuple(sorted(
        f"mutation-envelope:{node.layoutId}"
        for node in _walk(spec.layout)
        if node.constraints is not None
    ))
    active_owners = (*hard_ids, *envelope_ids)
    result = _solve_active(spec, frozenset(active_owners))
    if result.feasible or not compute_unsat_core or not result.search_complete:
        return result
    core = list(active_owners)
    oracle_calls = 0
    cache: dict[frozenset[str], FeasibilityResult] = {
        frozenset(active_owners): result,
    }

    def oracle(owners: frozenset[str]) -> FeasibilityResult:
        nonlocal oracle_calls
        cached = cache.get(owners)
        if cached is not None:
            return cached
        oracle_calls += 1
        solved = _solve_active(spec, owners)
        cache[owners] = solved
        return solved

    # Phase 1 is deliberately unbudgeted: it guarantees a useful, sound
    # inclusion-minimal fallback before the optional exponential phase starts.
    for constraint_id in tuple(core):
        reduced = frozenset(item for item in core if item != constraint_id)
        reduced_result = oracle(reduced)
        if not reduced_result.search_complete:
            return FeasibilityResult(
                False,
                {},
                iterations=result.iterations,
                branches_evaluated=result.branches_evaluated,
                max_residual=result.max_residual,
                reason_codes=("constraint.feasibility.search_limited",),
                search_complete=False,
                variable_count=result.variable_count,
                half_space_count=result.half_space_count,
                non_overlap_pairs=result.non_overlap_pairs,
                core_oracle_calls=oracle_calls,
                core_search_complete=False,
            )
        if not reduced_result.feasible:
            core.remove(constraint_id)

    inclusion_core = tuple(core)
    cardinality_core: tuple[str, ...] | None = None
    cardinality_checks = 0
    cardinality_complete = True
    # Search every smaller cardinality over the original owner universe.  The
    # first UNSAT subset is minimum-cardinality by construction, and tuple
    # ordering provides a stable tie break among equally small cores.
    for size in range(1, len(inclusion_core)):
        for candidate in combinations(active_owners, size):
            owners = frozenset(candidate)
            cached = cache.get(owners)
            if cached is None:
                if cardinality_checks >= cardinality_core_budget:
                    cardinality_complete = False
                    break
                cardinality_checks += 1
            candidate_result = oracle(owners)
            if not candidate_result.search_complete:
                cardinality_complete = False
                break
            if not candidate_result.feasible:
                cardinality_core = candidate
                break
        if cardinality_core is not None or not cardinality_complete:
            break

    selected_core = cardinality_core or inclusion_core
    core_kind = "cardinality-minimum" if cardinality_complete else "inclusion-minimal-fallback"
    core_reason = (
        "constraint.unsat_core.cardinality_minimum"
        if cardinality_complete
        else "constraint.unsat_core.inclusion_minimal_fallback"
    )
    return FeasibilityResult(
        False,
        {},
        unsat_core=selected_core,
        iterations=result.iterations,
        branches_evaluated=result.branches_evaluated,
        max_residual=result.max_residual,
        reason_codes=("constraint.feasibility.unsatisfied", core_reason),
        search_complete=True,
        variable_count=result.variable_count,
        half_space_count=result.half_space_count,
        non_overlap_pairs=result.non_overlap_pairs,
        unsat_core_kind=core_kind,
        core_oracle_calls=oracle_calls,
        core_search_complete=cardinality_complete,
    )


def _local_bounds(
    layout_id: str,
    solved: Mapping[str, SolvedRect],
    parents: Mapping[str, str | None],
) -> Bounds | None:
    parent_id = parents[layout_id]
    if parent_id is None:
        return None
    rect = solved[layout_id]
    parent = solved[parent_id]
    if parent.width <= _EPSILON or parent.height <= _EPSILON:
        return None
    values = (
        (rect.x - parent.x) / parent.width,
        (rect.y - parent.y) / parent.height,
        rect.width / parent.width,
        rect.height / parent.height,
    )
    x, y, width, height = (round(value, 9) for value in values)
    x = min(1, max(0, x))
    y = min(1, max(0, y))
    width = min(1 - x, max(1e-6, width))
    height = min(1 - y, max(1e-6, height))
    return Bounds(x=x, y=y, width=width, height=height)


def build_constraint_repair_plan(
    spec: SlideSpec,
    *,
    feasibility: FeasibilityResult | None = None,
) -> RepairPlan | None:
    """Return the deterministic minimum-change plan for an infeasible baseline.

    ``feasibility`` lets production callers reuse the exact solver result they
    timed and exposed through observability.  Keeping the default preserves the
    public convenience API while avoiding a second, unmeasured solver run in the
    authored-review executor.
    """

    result = feasibility if feasibility is not None else solve_constraints(spec)
    if not result.feasible or not result.search_complete or not result.optimality_proven:
        return None
    original = _absolute_bounds(spec)
    if all(
        max(abs(getattr(result.bounds[node_id], field) - getattr(rect, field)) for field in ("x", "y", "width", "height")) <= 2e-6
        for node_id, rect in original.items()
    ):
        return None
    parents = _parents(spec.layout)
    nodes = {node.layoutId: node for node in _walk(spec.layout)}
    operations: list[dict[str, object]] = []
    for layout_id in sorted(nodes):
        node = nodes[layout_id]
        if node.bounds is None:
            continue
        local = _local_bounds(layout_id, result.bounds, parents)
        if local is None:
            continue
        if max(abs(getattr(local, field) - getattr(node.bounds, field)) for field in ("x", "y", "width", "height")) <= 2e-6:
            continue
        allowed = set(node.constraints.allowedOperations or ()) if node.constraints else set()
        if allowed and "setBounds" not in allowed:
            # Mixed move+resize without setBounds is projected as two supported ops.
            position_changed = abs(local.x - node.bounds.x) > 2e-6 or abs(local.y - node.bounds.y) > 2e-6
            size_changed = abs(local.width - node.bounds.width) > 2e-6 or abs(local.height - node.bounds.height) > 2e-6
            if position_changed and "translateWithinParent" in allowed:
                operations.append({"op": "translateWithinParent", "target": layout_id, "value": {"x": local.x - node.bounds.x, "y": local.y - node.bounds.y}, "precondition": {"expectedBounds": node.bounds.model_dump()}})
            elif position_changed:
                return None
            if size_changed and "resizeWithinBounds" in allowed:
                expected = node.bounds.model_copy(
                    update={"x": local.x, "y": local.y}
                ) if position_changed else node.bounds
                operations.append({"op": "resizeWithinBounds", "target": layout_id, "width": local.width, "height": local.height, "precondition": {"expectedBounds": expected.model_dump()}})
            elif size_changed:
                return None
        else:
            operations.append({"op": "setBounds", "target": layout_id, "bounds": local.model_dump(), "precondition": {"expectedBounds": node.bounds.model_dump()}})
    if not operations or len(operations) > 32:
        return None
    common: dict[str, object] = {
        "$schema": REPAIR_PLAN_SCHEMA,
        "slideId": spec.slideId,
        "baseSpecRevision": spec.specRevision,
        "baseSpecHash": spec.digests.spec,
        "expectedContentDigest": spec.digests.content,
        "scope": list(dict.fromkeys(str(operation["target"]) for operation in operations)),
        "diagnosticRefs": sorted(constraint.constraintId for constraint in spec.constraints if constraint.strength == "hard"),
        "strategy": "minimal_change",
        "operations": operations,
        "limits": {"maxOperations": 32, "maxAffectedBlocks": 32, "maxLayoutDepth": 32},
        "preconditions": {"slideId": spec.slideId, "baseSpecRevision": spec.specRevision, "baseSpecHash": spec.digests.spec, "expectedContentDigest": spec.digests.content},
        "proposedBy": {"kind": "rule_engine", "policyVersion": SOLVER_POLICY_VERSION, "costVersion": "repair-cost/1.0"},
    }
    encoded = json.dumps(common, sort_keys=True, separators=(",", ":"), default=str)
    common["planId"] = "rp_solver_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    return RepairPlan.model_validate(common)


__all__ = [
    "FeasibilityResult",
    "SOLVER_POLICY_VERSION",
    "SolvedRect",
    "build_constraint_repair_plan",
    "solve_constraints",
]
