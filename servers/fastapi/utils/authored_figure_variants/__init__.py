"""Best-of-N candidates for an authored figure slide, without another LLM call.

The package is a thin facade over four small modules:

``markup``
    Finds the archetype figure in an authored document and rewrites only its
    density and emphasis classes.
``variants``
    Turns one authored document into the fixed, deduplicated candidate list.
``metrics``
    Reduces one render's browser telemetry to overflow, overlap, whitespace
    balance and achieved density.
``selection``
    Picks a candidate under the DOM gate's no-regression rule.
``operations``
    The closed corrections a design critic may request.
"""

from __future__ import annotations

from utils.authored_figure_variants.markup import (
    DENSITY_CLASSES,
    EMPHASIS_CLASS,
    FigureMarkup,
    canonical_figure_html,
    find_figure,
    has_figure_markup,
    same_figure_document,
    set_density_class,
    set_emphasis,
    shift_density,
)
from utils.authored_figure_variants.metrics import (
    INK_RATIO_BAND,
    CandidateMetrics,
    ink_ratio_penalty,
    measure_candidate,
)
from utils.authored_figure_variants.operations import (
    FigureOperation,
    OperationKind,
    OperationRefused,
    apply_operation,
    apply_operations,
)
from utils.authored_figure_variants.selection import (
    FigureSelection,
    RenderedCandidate,
    candidate_score,
    select_candidate,
)
from utils.authored_figure_variants.variants import (
    DEFAULT_FIGURE_CANDIDATES,
    MAX_FIGURE_CANDIDATES,
    FigureVariant,
    figure_variants,
)

__all__ = [
    "DEFAULT_FIGURE_CANDIDATES",
    "DENSITY_CLASSES",
    "EMPHASIS_CLASS",
    "INK_RATIO_BAND",
    "MAX_FIGURE_CANDIDATES",
    "CandidateMetrics",
    "FigureMarkup",
    "FigureOperation",
    "FigureSelection",
    "FigureVariant",
    "OperationKind",
    "OperationRefused",
    "RenderedCandidate",
    "apply_operation",
    "apply_operations",
    "canonical_figure_html",
    "candidate_score",
    "figure_variants",
    "find_figure",
    "has_figure_markup",
    "ink_ratio_penalty",
    "measure_candidate",
    "same_figure_document",
    "select_candidate",
    "set_density_class",
    "set_emphasis",
    "shift_density",
]
