"""Measured critique inputs behind the authored generation facade."""

from __future__ import annotations


def _dom_repair_detail(
    category: str, message: str, qids: object
) -> str:
    """One repair instruction that says which elements the gate measured.

    The gate reports per element and the caller collapses identical sentences,
    so without the element names a slide with four overflowing boxes is
    repaired from "something overflows".  The names come from the measurement,
    fit inside the detail budget, and the count is kept when they do not.
    """

    names = [
        str(value).strip()
        for value in (qids if isinstance(qids, (list, tuple)) else ())
        if str(value).strip()
    ]
    head = f"DOM {category}: {message}."
    tail = " Keep every QID, semantic role, source owner and header text."
    # Name as many elements as fit whole. Slicing the finished sentence would
    # cut a QID in half, and half a QID points at nothing.
    for count in range(len(names), 0, -1):
        listed = ", ".join(names[:count])
        remaining = len(names) - count
        elements = listed + (f" +{remaining} more" if remaining else "")
        candidate = f"{head} Elements: {elements}.{tail}"
        if len(candidate) <= _MAX_CRITIQUE_DETAIL:
            return candidate
    return (head + tail)[:_MAX_CRITIQUE_DETAIL]

def _bounded_critique_issues(
    issues: list[CritiqueIssue], *, slide_index: int
) -> list[CritiqueIssue]:
    """Fit one slide's gate findings into the critique contract.

    The gate reports per element, so the four boxes that overflowed slide 10 of
    operation b3e18cc2 produced four identical sentences.  Collapsing those is
    what makes the cap bite only on genuinely different findings, and it is
    also the better repair prompt: one instruction beats the same instruction
    four times.  Anything still over the cap keeps the gate's own reporting
    order -- every issue it raises here carries the same severity -- and the
    drop is logged rather than raised, because a slide with many defects is
    exactly the slide that must reach repair.
    """

    unique: list[CritiqueIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.type, issue.severity, issue.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    if len(unique) <= _MAX_CRITIQUE_ISSUES:
        return unique
    # A single container escaping the canvas usually explains the many child
    # clipping reports that follow it. Keep structural/root-cause findings in
    # the bounded prompt before their downstream symptoms so the patch planner
    # can reflow the existing grid instead of nudging individual text leaves.
    def repair_priority(issue: CritiqueIssue) -> int:
        detail = issue.detail.casefold()
        if "off_canvas" in detail or "off-canvas" in detail:
            return 0
        if "text_escapes_card" in detail:
            return 1
        if "overlap" in detail:
            return 2
        if "clipping" in detail:
            return 3
        return 4

    unique = sorted(unique, key=repair_priority)
    LOGGER.warning(
        "[authored] slide %d reported %d distinct gate issues; repairing the "
        "first %d",
        slide_index + 1,
        len(unique),
        _MAX_CRITIQUE_ISSUES,
    )
    return unique[:_MAX_CRITIQUE_ISSUES]

def _capacity_gate_initial_critiques(
    gate_failures: list[dict[str, object]],
    slide_indices: list[int],
    roles: list[str],
    *,
    allow_text_reduction: bool = True,
) -> list[SlideCritique | None]:
    """Make deterministic capacity failures start the existing one-cycle repair."""

    details_by_slide: dict[int, list[CritiqueIssue]] = {}
    dom_groups: dict[int, dict[tuple[str, str, str], list[str]]] = {}
    for failure in gate_failures:
        slide_index = failure.get("slide_index")
        if not isinstance(slide_index, int):
            continue
        for issue in failure.get("issues", ()):
            if not isinstance(issue, dict):
                continue
            category = str(issue.get("category") or "other").strip().lower()
            message = str(issue.get("message") or issue.get("detail") or "").strip()
            if not message:
                continue
            if category == "capacity":
                instructions = capacity_repair_instructions(
                    roles[slide_index],
                    (message,),
                    allow_text_reduction=allow_text_reduction,
                )
                details_by_slide.setdefault(slide_index, []).extend(
                    CritiqueIssue(type="overflow", severity="high", detail=detail)
                    for detail in instructions
                )
                continue
            issue_type = "overlap" if category == "overlap" else "overflow"
            # One instruction per distinct defect, naming every element the
            # gate measured it on. Reported per element, the same sentence
            # would otherwise arrive four times and say nothing about where.
            group = dom_groups.setdefault(slide_index, {}).setdefault(
                (issue_type, category, message), []
            )
            for value in issue.get("qids") or ():
                name = str(value).strip()
                if name and name not in group:
                    group.append(name)
    for slide_index, groups in dom_groups.items():
        details_by_slide.setdefault(slide_index, []).extend(
            CritiqueIssue(
                type=issue_type,
                severity="high",
                detail=_dom_repair_detail(category, message, qids),
            )
            for (issue_type, category, message), qids in groups.items()
        )
    return [
        (
            SlideCritique(
                needs_fix=True,
                issues=_bounded_critique_issues(
                    details_by_slide[index], slide_index=index
                ),
            )
            if details_by_slide.get(index)
            else None
        )
        for index in slide_indices
    ]


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value
