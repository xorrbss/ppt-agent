"""Cluster/runner concurrency knobs kept behind the get_env facade.

Split out of ``utils/get_env.py`` (a frozen file-size exception) rather than
grown in place; ``utils/get_env.py`` re-exports these so callers keep using
the single ``utils.get_env`` facade.

All four values are read once at import/runner-construction time (module
load for the authored job-service and job-runtime constants, and
``WorkspaceOperationRunner.__init__``/``default_trusted_worker_scheduler_policy``
for the other two). Changing the backing environment variable or managed
setting therefore needs a process restart to take effect -- there is no
runtime-settings hot path for these four knobs.
"""

from __future__ import annotations

from utils.runtime_settings import RuntimeSettings


def get_authored_job_concurrency_env(settings: RuntimeSettings | None = None) -> int:
    """In-process authored job semaphore size (default 1)."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("AUTHORED_JOB_CONCURRENCY", settings) or 1


def get_authored_cluster_job_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Database-backed cluster-wide authored job cap.

    Falls back to ``AUTHORED_JOB_CONCURRENCY`` (default 1) so adding FastAPI
    workers does not silently multiply provider concurrency and spend.
    """
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "AUTHORED_CLUSTER_JOB_CONCURRENCY", settings
    ) or get_authored_job_concurrency_env(settings)


def get_workspace_operation_max_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Max concurrently-running workspace operations per runner (default 2)."""
    from utils.get_env import _optional_positive_int_setting

    return (
        _optional_positive_int_setting("WORKSPACE_OPERATION_MAX_CONCURRENCY", settings)
        or 2
    )


def get_public_export_scheduler_global_cap_env(
    settings: RuntimeSettings | None = None,
) -> int:
    """Trusted-executor scheduler global concurrency cap (default 4)."""
    from utils.get_env import _optional_positive_int_setting

    return (
        _optional_positive_int_setting("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", settings)
        or 4
    )


def get_svg_author_concurrency_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit SVG author-call concurrency, or ``None`` for the profile default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_AUTHOR_CONCURRENCY", settings)


def get_svg_author_concurrency_source_reflow_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit SVG author-call concurrency for source-reflow decks."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_AUTHOR_CONCURRENCY_SOURCE_REFLOW", settings
    )


def get_svg_render_concurrency_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit SVG page-render concurrency, or ``None`` for the profile default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_RENDER_CONCURRENCY", settings)


def get_svg_render_concurrency_source_reflow_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit SVG page-render concurrency for source-reflow decks."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_RENDER_CONCURRENCY_SOURCE_REFLOW", settings
    )


def get_svg_illustration_budget_env(settings: RuntimeSettings | None = None) -> int | None:
    """Explicit per-deck illustration budget shared by the SVG driver's asset
    stage, or ``None`` for the profile default. Name agreed with c3-assets so
    the illustration stage can read the same field once it moves into
    ``generation_core/assets/``.
    """
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting("SVG_ILLUSTRATION_BUDGET", settings)


SVG_ILLUSTRATION_MODES = ("off", "vector", "hybrid")


def get_svg_illustration_mode_env(settings: RuntimeSettings | None = None) -> str:
    """How the SVG driver's asset stage makes the deck's pictures.

    ``off`` keeps the image-provider scenes only; ``vector`` has the model draw
    scenes, cover/section pictures, step pictures and before/after pairs as
    vector SVG; ``hybrid`` does the same but leaves a scene the plan marked as
    a photograph to the image provider when one is configured. Read when a
    deck's asset offer is built; a resumed deck replays its persisted offer.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_ILLUSTRATION_MODE", settings) or "").strip().lower()
    return value if value in SVG_ILLUSTRATION_MODES else "off"


def get_content_structure_policy_scope_env(settings: RuntimeSettings | None = None) -> str:
    """Which decks get the content structure policy (``utils/content_structure_policy.py``):
    ``off`` (the default), ``template`` (a template-redesign deck) or ``svg`` (every svg deck).
    Read when a deck is admitted and when its pages are authored.

    Internal, opt-in knob deliberately outside ``constants/settings_registry.json``
    (like ``SVG_ILLUSTRATION_MODE``): the registry holds managed/public settings, and
    this one is experimental and off by default, because turning it on measured worse (see
    ``docs/content-structure-policy-20261104.md``). It is accounted for in
    ``tests/support/internal_env_names.py``, and resolves as a process-start env value.
    """
    from utils.content_structure_policy import normalize_scope
    from utils.get_env import get_setting_env

    return normalize_scope(get_setting_env("CONTENT_STRUCTURE_POLICY_SCOPE", settings))


def get_content_structure_policy_parts_env(settings: RuntimeSettings | None = None) -> tuple[str, ...]:
    """Which parts of the content structure policy a deck in its scope gets (comma list):
    ``tables``, ``connectors``. Unset, empty or naming nothing known: both (the policy as it was
    before the parts were split). Has no effect while the scope is ``off``. Internal, like the scope.
    """
    from utils.content_structure_policy import normalize_parts
    from utils.get_env import get_setting_env

    return normalize_parts(get_setting_env("CONTENT_STRUCTURE_POLICY_PARTS", settings))


SVG_ILLUSTRATION_PARTS = ("cover", "scene", "spots", "pairs", "pictograph")
DEFAULT_SVG_ILLUSTRATION_PARTS = ("cover", "scene")


def get_svg_illustration_parts_env(settings: RuntimeSettings | None = None) -> tuple[str, ...]:
    """Which model-drawn picture kinds a vector-mode deck gets (comma list).

    ``cover``, ``scene``, ``spots`` (step pictures), ``pairs`` (before/after) and
    ``pictograph`` (a percentage as ten people, composed, not drawn).
    Unset, empty or naming nothing known: ``cover,scene``. Unknown names are ignored.
    """
    from utils.get_env import get_setting_env

    raw = get_setting_env("SVG_ILLUSTRATION_PARTS", settings) or ""
    parts = tuple(part for part in SVG_ILLUSTRATION_PARTS
                  if part in {item.strip().lower() for item in raw.split(",")})
    return parts or DEFAULT_SVG_ILLUSTRATION_PARTS


def get_svg_source_output_view_env(settings: RuntimeSettings | None = None) -> str | None:
    """Raw ``SVG_SOURCE_OUTPUT_VIEW`` text (unset = v1; explicit off disables it).

    Not a concurrency knob, but an SVG driver knob read the same way: only at admission of a
    whole-page source restyle job, whose frozen snapshot then pins the parsed view
    (``services/svg_author_output_view.py`` owns the grammar), so a change affects new jobs only.
    """
    from utils.get_env import get_setting_env

    value = get_setting_env("SVG_SOURCE_OUTPUT_VIEW", settings)
    return "v1" if value is None else value


def get_svg_illustration_timeout_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-illustration timeout in seconds, or ``None`` for the default."""
    from utils.get_env import _optional_positive_int_setting

    return _optional_positive_int_setting(
        "SVG_ILLUSTRATION_TIMEOUT_SECONDS", settings
    )


def get_svg_template_body_exemplars_env(settings: RuntimeSettings | None = None) -> str:
    """How much of a template's body composition a template-redesign author sees
    (``SVG_TEMPLATE_BODY_EXEMPLARS``): ``on`` the composition grammar in the rules and one or two
    exemplar outlines of the template's own body pages per body page (also when unset: the default,
    chosen on a paired measurement, docs/template-body-composition-20261105.md), ``grammar`` the
    grammar only, ``off`` (or an unknown value) neither. A template registered without body
    composition gets neither whatever the value.

    Guidance only (``services/svg_templates/body_composition.py``), never a layout. Internal knob read
    when a page is authored, listed in ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_BODY_EXEMPLARS", settings) or "").strip().lower()
    if value in {"", "on", "true", "1"}:
        return "on"
    return "grammar" if value == "grammar" else "off"


def get_svg_template_outline_sections_env(settings: RuntimeSettings | None = None) -> bool:
    """Whether a template-redesign deck's outline is asked for the template's page structure
    (``SVG_TEMPLATE_OUTLINE_SECTIONS``: ``on``, also when unset: the default since the paired
    measurement in docs/template-a-visuals-progress-20261105.md; ``off`` or an unknown value: off).

    On, the outline prompt adds :data:`utils.svg_template_outline.TEMPLATE_SECTIONS_CLAUSE`:
    a key-message line and two to four ``###`` sub-sections per content slide, each holding
    its own facts, so the body author gets sections to compose (measured 2026-11-05: the
    judge counted 0 sections on 13 of 20 generated pages while every outline page held one
    ``##`` title and bullets only). Internal knob read when the outline is requested, listed
    in ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_OUTLINE_SECTIONS", settings) or "").strip().lower()
    return value in {"", "on", "true", "1"}


def get_svg_template_visual_guidance_env(settings: RuntimeSettings | None = None) -> bool:
    """Whether a template-redesign author is told to use the template's body vocabulary
    (``SVG_TEMPLATE_VISUAL_GUIDANCE``: ``on`` / ``off``; unset or unknown: off).

    On, the frozen composition guidance of a template-redesign snapshot names the
    template's components where the content has their shape instead of saying that icons
    and visual difference are not evidence of improvement, and the composition grammar's
    density line asks for secondary components and full explanations instead of enlarged
    components (``services/svg_templates/generation.py``, ``body_composition.py``). Prompt
    only. Internal knob read at admission and when a page is authored, listed in
    ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_VISUAL_GUIDANCE", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_template_step_pictures_env(settings: RuntimeSettings | None = None) -> bool:
    """Whether a template-redesign deck gets model-drawn step pictures
    (``SVG_TEMPLATE_STEP_PICTURES``: ``on`` / ``off``; unset or unknown: off).

    On, and only while ``SVG_ILLUSTRATION_MODE`` is off, the asset offer of a template-redesign
    deck runs in vector mode with the ``spots`` part alone (``drivers/svg/asset_slots.py``): a
    process, cycle, timeline or roadmap page with three to six planned steps gets one small
    vector picture per step, drawn in the deck palette (``assets/vector_offer.py``), offered as
    optional slots; scenes are demoted to icons as in off mode and no cover picture is drawn.
    Measured 2026-11-05 on template A: the judge's visuals axis stayed at 2.2 with icons only
    ("no charts, diagrams or figures like the anchors") while the template's own pages carry
    small pictures above their step boxes. Internal knob read when the offer is built, listed in
    ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_STEP_PICTURES", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_common_header_default_env(settings: RuntimeSettings | None = None) -> str:
    """Header of a fresh prompt-only svg request that names no ``common_header_mode``
    (``SVG_COMMON_HEADER_DEFAULT``): ``designed`` (also when unset or unknown) the model-designed
    header, ``reduced`` the deterministic reduced header with its cover exception
    (``services/svg_common_header_design_plan.py``, decision D1 in
    ``docs/svg-mode-improvement-plan-20261105.md``).

    Read at admission only: the pinned mode and the sealed snapshot carry the choice, so a change
    affects new jobs only.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_COMMON_HEADER_DEFAULT", settings) or "").strip().lower()
    return "reduced" if value == "reduced" else "designed"


def get_svg_template_body_renders_env(settings: RuntimeSettings | None = None) -> bool:
    """Whether template registrations store masked renders of the template's own body pages and newly
    admitted template-redesign decks send them to the author (``SVG_TEMPLATE_BODY_RENDERS``: ``on`` /
    ``off``; unset or unknown: off). Measured 2026-11-05: no effect on the judge; stays off.

    Read at registration (off: no masking, OCR or render artifact; a template registered then has no
    renders, so turning the knob on later needs the template registered again) and once at
    admission, frozen into the style snapshot (``template_body_renders``), so a resumed, retried or
    remade page of the same deck follows the deck's admission (``services/svg_templates/body_renders.py``;
    docs/template-body-renders-design-20261105.md).
    Internal knob, listed in ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_BODY_RENDERS", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_template_body_render_excluded_pages_env(settings: RuntimeSettings | None = None) -> frozenset[int]:
    """Source pages (1-based slide numbers) a template registration renders no body page for
    (``SVG_TEMPLATE_BODY_RENDER_EXCLUDED_PAGES``, a comma list such as ``9,66``; unset: none).

    Measurement only: the vision judge's score-5 anchors are original template pages, and an author
    shown a render of an anchor would be shown the judge's answer. Unknown or non-positive entries
    are ignored. Read at registration; the exemplar outlines themselves are unchanged. Internal knob,
    listed in ``tests/support/internal_env_names.py``.
    """
    from utils.get_env import get_setting_env

    raw = get_setting_env("SVG_TEMPLATE_BODY_RENDER_EXCLUDED_PAGES", settings) or ""
    return frozenset(int(item) for item in (part.strip() for part in raw.split(",")) if item.isdigit() and int(item) > 0)


def get_svg_native_chart_integrity_v5_env(settings: RuntimeSettings | None = None) -> bool:
    """Opt fresh SVG admissions into chart-integrity policy v5; default off.

    The selected requirements revision is pinned once. Existing v2/v3/v4
    snapshots and non-SVG engines retain their admitted interpretation.
    """
    from utils.get_env import get_setting_env
    value = (get_setting_env("SVG_NATIVE_CHART_INTEGRITY_V5", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_server_charts_env(settings: RuntimeSettings | None = None) -> bool:
    """Server-rendered bar/column charts for fresh SVG admissions (``SVG_SERVER_CHARTS``); default off.

    Admission freezes the policy into the style snapshot; existing decks never
    consult this process setting.
    """
    from utils.get_env import get_setting_env
    value = (get_setting_env("SVG_SERVER_CHARTS", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_template_pictures_env(settings: RuntimeSettings | None = None) -> bool:
    """Enable vector pictures for fresh template-redesign admissions; default off.

    Admission freezes the effective policy. Existing decks never consult this
    process setting when authoring, resuming or remaking their pages.
    """
    from utils.get_env import get_setting_env
    value = (get_setting_env("SVG_TEMPLATE_PICTURES", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_template_content_sizing_env(settings: RuntimeSettings | None = None) -> bool:
    """Enable content-sized body guidance at fresh admission; default off."""
    from utils.get_env import get_setting_env
    value = (get_setting_env("SVG_TEMPLATE_CONTENT_SIZING", settings) or "").strip().lower()
    return value in {"on", "true", "1"}


def get_svg_template_component_style_env(settings: RuntimeSettings | None = None) -> bool:
    """Whether a template-redesign deck's author rules carry the template's body component styles
    (``SVG_TEMPLATE_COMPONENT_STYLE``: ``on`` / ``off``; unset or unknown: off).

    Style only (chip, label, bar, card, panel, table header looks and the type-size ladder,
    ``services/svg_templates/component_style.py``), never a layout. Internal knob read when a page
    is authored, listed in ``tests/support/internal_env_names.py``; see
    ``docs/template-component-style-20261104.md`` for the measurement behind the default.
    """
    from utils.get_env import get_setting_env

    value = (get_setting_env("SVG_TEMPLATE_COMPONENT_STYLE", settings) or "").strip().lower()
    return value in {"on", "true", "1"}
