"""Message keys for every user-facing string the backend emits.

The backend emits **codes**, the frontend renders **text**: this module is the
one place that decides which keys exist, and ``UserMessage`` is the one value
type every transport serializes.  A key is a dotted, lowercase identifier
(``generation.stage.outline``); the Next.js catalog resolves it under the
``backend`` namespace with dots replaced by underscores
(``backend.generation_stage_outline``) and formats ``params`` with ICU.

Every key carries an English fallback so that clients which only read the
legacy text field (``message``, ``label``, ``summary``, ``title``, ``content``)
keep working.  Korean lives in ``servers/nextjs/i18n/messages/ko.json`` only.
``tests/unit/test_no_hangul_in_backend_messages.py`` enforces the invariant:
a Hangul string literal outside the allow-listed prompt/heuristic modules is a
test failure, so new user-facing text must be added here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class UserMessageKey(StrEnum):
    # Authored generation progress narration.
    GENERATION_STAGE_OUTLINE = "generation.stage.outline"
    GENERATION_STAGE_AUTHOR = "generation.stage.author"
    GENERATION_STAGE_AUTHOR_COUNTED = "generation.stage.author.counted"
    GENERATION_STAGE_ILLUSTRATE = "generation.stage.illustrate"
    GENERATION_STAGE_ILLUSTRATE_COUNTED = "generation.stage.illustrate.counted"
    GENERATION_STAGE_RENDER = "generation.stage.render"
    GENERATION_STAGE_RENDER_COUNTED = "generation.stage.render.counted"
    GENERATION_STAGE_PERSIST = "generation.stage.persist"
    GENERATION_STAGE_PERSIST_COUNTED = "generation.stage.persist.counted"
    GENERATION_STAGE_REPAIR = "generation.stage.repair"
    GENERATION_STAGE_CHECKPOINT = "generation.stage.checkpoint"
    GENERATION_STAGE_EXPORT = "generation.stage.export"
    GENERATION_STAGE_PUBLICATION = "generation.stage.publication"
    GENERATION_PREPARING_SLIDES = "generation.preparing_slides"
    GENERATION_SAVING_SLIDES = "generation.saving_slides"
    GENERATION_WRITING_SLIDES = "generation.writing_slides"
    GENERATION_WRITING_SLIDES_STAGED = "generation.writing_slides.staged"

    # Raster (source PPTX screen) analysis operation.
    RASTER_ANALYSIS_PRESERVE = "raster.analysis.preserve"
    RASTER_ANALYSIS_REDESIGN = "raster.analysis.redesign"
    RASTER_ANALYSIS_REGIONS = "raster.analysis.regions"
    RASTER_ANALYSIS_REDESIGN_ELAPSED = "raster.analysis.redesign.elapsed"

    # SVG user template registration.
    SVG_TEMPLATE_ANALYSIS_CHECKING = "svg_template.analysis.checking"
    RASTER_ANALYSIS_REGIONS_ELAPSED = "raster.analysis.regions.elapsed"
    RASTER_ANALYSIS_PERSISTING = "raster.analysis.persisting"

    # Page remake (single page, batch, and SVG).
    REMAKE_PAGE_COMMAND = "remake.page.command"
    REMAKE_PAGE_QUEUED = "remake.page.queued"
    REMAKE_PAGE_RUNNING = "remake.page.running"
    REMAKE_PAGE_READY = "remake.page.ready"
    REMAKE_PAGE_FAILED = "remake.page.failed"
    REMAKE_PAGE_APPLIED = "remake.page.applied"
    REMAKE_BATCH_COMMAND = "remake.batch.command"
    REMAKE_BATCH_QUEUED = "remake.batch.queued"
    REMAKE_BATCH_RUNNING = "remake.batch.running"
    REMAKE_BATCH_READY = "remake.batch.ready"
    REMAKE_BATCH_PARTIAL = "remake.batch.partial"
    REMAKE_SVG_RUNNING = "remake.svg.running"
    REMAKE_SVG_READY = "remake.svg.ready"

    # Quality review task narration.
    QUALITY_REVIEW_COMMAND = "quality.review.command"
    QUALITY_REVIEW_QUEUED = "quality.review.queued"
    QUALITY_REVIEW_PREPARING_RENDER = "quality.review.preparing_render"
    QUALITY_REVIEW_RENDERING = "quality.review.rendering"
    QUALITY_REVIEW_CHECKING = "quality.review.checking"
    QUALITY_REVIEW_NO_ISSUES = "quality.review.no_issues"
    QUALITY_REVIEW_ISSUES_FOUND = "quality.review.issues_found"
    QUALITY_REVIEW_FIXING = "quality.review.fixing"
    QUALITY_REVIEW_VERIFYING = "quality.review.verifying"
    QUALITY_REVIEW_FIXED = "quality.review.fixed"
    QUALITY_REVIEW_FIXED_REMAINING = "quality.review.fixed.remaining"
    QUALITY_REVIEW_NO_CHANGES = "quality.review.no_changes"
    QUALITY_REVIEW_NO_CHANGES_REMAINING = "quality.review.no_changes.remaining"
    QUALITY_REVIEW_NO_CONFIRMED_CHANGES = "quality.review.no_confirmed_changes"

    # Version history labels and results.
    VERSION_LABEL_PAGE_REMAKE = "version.label.page_remake"
    VERSION_LABEL_BATCH_REMAKE = "version.label.batch_remake"
    VERSION_LABEL_BEFORE_RESTORE = "version.label.before_restore"
    VERSION_LABEL_BEFORE_SLIDESPEC = "version.label.before_slidespec"
    VERSION_LABEL_BEFORE_QUALITY_REVIEW = "version.label.before_quality_review"
    VERSION_RESTORE_SUMMARY = "version.restore.summary"

    # Upload / document endpoints.
    FILES_DRM_BLOCKED = "files.drm_blocked"
    FILES_LIBREOFFICE_REQUIRED = "files.libreoffice_required"
    FILES_FORMAT_REQUIRES_LIBREOFFICE = "files.format_requires_libreoffice"
    SLIDE_AUTHORED_NOT_EDITABLE = "slide.authored_not_editable"

    # Workspace operation titles are derived from the operation kind:
    # ``workspace.operation.<kind>``.  See ``workspace_operation_title_key``.


_FALLBACK_TEXT: dict[UserMessageKey, str] = {
    UserMessageKey.GENERATION_STAGE_OUTLINE: "Preparing the deck structure and writing brief.",
    UserMessageKey.GENERATION_STAGE_AUTHOR: "Writing slide content.",
    UserMessageKey.GENERATION_STAGE_AUTHOR_COUNTED: "Writing slide content · {completed}/{total} slides",
    UserMessageKey.GENERATION_STAGE_ILLUSTRATE: "Composing visual elements.",
    UserMessageKey.GENERATION_STAGE_ILLUSTRATE_COUNTED: "Composing visual elements · {completed}/{total} slides",
    UserMessageKey.GENERATION_STAGE_RENDER: "Rendering slides.",
    UserMessageKey.GENERATION_STAGE_RENDER_COUNTED: "Rendering slides · {completed}/{total} slides",
    UserMessageKey.GENERATION_STAGE_PERSIST: "Saving slide checkpoints.",
    UserMessageKey.GENERATION_STAGE_PERSIST_COUNTED: "Saving slide checkpoints · {completed}/{total} slides",
    UserMessageKey.GENERATION_STAGE_REPAIR: "Repairing only the slides with problems.",
    UserMessageKey.GENERATION_STAGE_CHECKPOINT: "Checking the final quality evidence.",
    UserMessageKey.GENERATION_STAGE_EXPORT: "Creating the PowerPoint file.",
    UserMessageKey.GENERATION_STAGE_PUBLICATION: "Publishing the generated deck.",
    UserMessageKey.GENERATION_PREPARING_SLIDES: "Preparing slides.",
    UserMessageKey.GENERATION_SAVING_SLIDES: "Saving the generated slides.",
    UserMessageKey.GENERATION_WRITING_SLIDES: "Writing slides · {completed}/{total} slides",
    UserMessageKey.GENERATION_WRITING_SLIDES_STAGED: "Writing slides · {completed}/{total} slides · {stage}",
    UserMessageKey.SVG_TEMPLATE_ANALYSIS_CHECKING: "Checking the template material.",
    UserMessageKey.RASTER_ANALYSIS_PRESERVE: "Preserving the source screens in order.",
    UserMessageKey.RASTER_ANALYSIS_REDESIGN: "Rebuilding the source screens into editable text and tables.",
    UserMessageKey.RASTER_ANALYSIS_REGIONS: "Analysing the source page screens and text regions.",
    UserMessageKey.RASTER_ANALYSIS_REDESIGN_ELAPSED: (
        "Rebuilding the source screens into editable text and tables… ({elapsed}s elapsed)"
    ),
    UserMessageKey.RASTER_ANALYSIS_REGIONS_ELAPSED: (
        "Analysing the source page screens and text regions… ({elapsed}s elapsed)"
    ),
    UserMessageKey.RASTER_ANALYSIS_PERSISTING: "Saving the source regions and analysis results for review.",
    UserMessageKey.REMAKE_PAGE_COMMAND: "Preview a remake of page {page}",
    UserMessageKey.REMAKE_PAGE_QUEUED: "Queued a new version of page {page}.",
    UserMessageKey.REMAKE_PAGE_RUNNING: "Creating a new version of page {page}.",
    UserMessageKey.REMAKE_PAGE_READY: "The new version preview of page {page} is ready.",
    UserMessageKey.REMAKE_PAGE_FAILED: "The new page version could not be created.",
    UserMessageKey.REMAKE_PAGE_APPLIED: "Applied the new version of page {page}.",
    UserMessageKey.REMAKE_BATCH_COMMAND: "Preview a remake of {total} selected pages",
    UserMessageKey.REMAKE_BATCH_QUEUED: "Queued new versions of {total} pages.",
    UserMessageKey.REMAKE_BATCH_RUNNING: "Creating new versions of {total} pages.",
    UserMessageKey.REMAKE_BATCH_READY: "New version previews of {total} pages are ready.",
    UserMessageKey.REMAKE_BATCH_PARTIAL: "Created new versions of {completed} of {total} pages.",
    UserMessageKey.REMAKE_SVG_RUNNING: "Writing the SVG for page {page}.",
    UserMessageKey.REMAKE_SVG_READY: "The SVG preview of page {page} is ready.",
    UserMessageKey.QUALITY_REVIEW_COMMAND: "Review the quality of the AI-authored presentation",
    UserMessageKey.QUALITY_REVIEW_QUEUED: "Queued the quality review.",
    UserMessageKey.QUALITY_REVIEW_PREPARING_RENDER: "Preparing slide rendering.",
    UserMessageKey.QUALITY_REVIEW_RENDERING: "Rendering {count} slides.",
    UserMessageKey.QUALITY_REVIEW_CHECKING: "Checking clipping, overlap, alignment, and contrast in parallel.",
    UserMessageKey.QUALITY_REVIEW_NO_ISSUES: "Review complete. No slides need changes.",
    UserMessageKey.QUALITY_REVIEW_ISSUES_FOUND: "Review complete. Found issues on {count} slides.",
    UserMessageKey.QUALITY_REVIEW_FIXING: "Automatically fixing the {count} slides with issues.",
    UserMessageKey.QUALITY_REVIEW_VERIFYING: "Re-verifying the fixes.",
    UserMessageKey.QUALITY_REVIEW_FIXED: "Review complete. Fixed {count} slides.",
    UserMessageKey.QUALITY_REVIEW_FIXED_REMAINING: "Review complete. Fixed {count} slides. Some issues remain.",
    UserMessageKey.QUALITY_REVIEW_NO_CHANGES: "Review complete, but no automatic fix could be produced.",
    UserMessageKey.QUALITY_REVIEW_NO_CHANGES_REMAINING: (
        "Review complete, but no automatic fix could be produced. Some issues remain."
    ),
    UserMessageKey.QUALITY_REVIEW_NO_CONFIRMED_CHANGES: "Review complete, but no automatic fix was confirmed.",
    UserMessageKey.VERSION_LABEL_PAGE_REMAKE: "Page remake: {page}",
    UserMessageKey.VERSION_LABEL_BATCH_REMAKE: "Batch page remake",
    UserMessageKey.VERSION_LABEL_BEFORE_RESTORE: "Autosave before restore",
    UserMessageKey.VERSION_LABEL_BEFORE_SLIDESPEC: "Before SlideSpec activation",
    UserMessageKey.VERSION_LABEL_BEFORE_QUALITY_REVIEW: "Before quality review",
    UserMessageKey.VERSION_RESTORE_SUMMARY: "Restored the selected version.",
    UserMessageKey.FILES_DRM_BLOCKED: (
        "'{filename}' is protected by Fasoo DRM and cannot be read. Remove the DRM "
        "or save it from PowerPoint as a DRM-free .pptx file, then upload it again."
    ),
    UserMessageKey.FILES_LIBREOFFICE_REQUIRED: "LibreOffice, required to convert '{filename}', was not found.",
    UserMessageKey.FILES_FORMAT_REQUIRES_LIBREOFFICE: (
        "'{filename}' cannot be converted without LibreOffice. Convert it to PDF "
        "before uploading, or install LibreOffice."
    ),
    UserMessageKey.SLIDE_AUTHORED_NOT_EDITABLE: (
        "This slide was generated in AI-authored mode and does not support in-app "
        "editing. Edit the exported PPTX in PowerPoint."
    ),
}

WORKSPACE_OPERATION_TITLE_KEY_PREFIX = "workspace.operation."


def workspace_operation_title_key(kind: str) -> str:
    """Key under which the frontend resolves an operation kind's display title."""

    return f"{WORKSPACE_OPERATION_TITLE_KEY_PREFIX}{kind}"


def render_user_message_text(key: UserMessageKey, params: Mapping[str, Any]) -> str:
    """Render the English fallback for ``key`` (``str.format`` placeholders)."""

    template = _FALLBACK_TEXT[key]
    if not params:
        return template
    return template.format(**{name: str(value) for name, value in params.items()})


@dataclass(frozen=True, slots=True)
class UserMessage:
    """A user-facing message: a key plus the parameters its text needs."""

    key: UserMessageKey
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """English fallback text for clients that only read the text field."""

        return render_user_message_text(self.key, self.params)

    def fields(self, name: str = "message") -> dict[str, Any]:
        """``{name: text, name_key: key, name_params: params}`` for a payload."""

        payload: dict[str, Any] = {name: self.text, f"{name}_key": str(self.key)}
        if self.params:
            payload[f"{name}_params"] = dict(self.params)
        return payload

    def __str__(self) -> str:
        return self.text


def user_message(key: UserMessageKey, **params: Any) -> UserMessage:
    """Build a ``UserMessage``; every parameter the template names is required."""

    return UserMessage(key=key, params=params)


UserMessageLike = str | UserMessage | None


def message_text(value: UserMessageLike) -> str | None:
    """The text field for a transport that still stores a plain string."""

    if value is None:
        return None
    return value.text if isinstance(value, UserMessage) else value


def message_fields(value: UserMessageLike, name: str = "message") -> dict[str, Any]:
    """Serialize ``value`` into ``name``/``name_key``/``name_params`` fields.

    A plain string only fills ``name``; ``None`` fills nothing.
    """

    if value is None:
        return {}
    if isinstance(value, UserMessage):
        return value.fields(name)
    return {name: value}


class _KeyedMessageRecord(Protocol):
    message: str | None
    message_key: str | None
    message_params: dict[str, Any] | None


def set_record_message(record: _KeyedMessageRecord, value: UserMessageLike) -> None:
    """Store ``value`` on a persisted row that has ``message``/``message_key``/``message_params``."""

    record.message = message_text(value)
    if isinstance(value, UserMessage):
        record.message_key = str(value.key)
        record.message_params = dict(value.params) or None
    else:
        record.message_key = None
        record.message_params = None


__all__ = [
    "UserMessage",
    "UserMessageKey",
    "UserMessageLike",
    "WORKSPACE_OPERATION_TITLE_KEY_PREFIX",
    "message_fields",
    "message_text",
    "render_user_message_text",
    "set_record_message",
    "user_message",
    "workspace_operation_title_key",
]
