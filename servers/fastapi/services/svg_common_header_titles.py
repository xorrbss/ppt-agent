"""Prepare missing external Markdown titles once, behind durable spend fences.

Only title metadata changes. The existing header deck-plan checkpoint stores a
started marker before I/O and a validated result before returning; an unfinished
or rejected marker is never a reason to issue another provider request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import re

from sqlalchemy import select, update

from models.sql.public_admission import AuthoredDeckPlanCheckpoint
from services.authored_deck_plan_checkpoint import canonical_checkpoint_digest
from services.ppt_writing_policy import ProtectedSpan, scan_visible_text
from services.provider_usage_instrumentation import provider_usage_scope
from services.generation_core.drivers.svg.timing import observe_setup
from services.svg_common_header import common_header_source_digest
from services.svg_common_header_title_budget import (
    INTERNAL_HEADER_TITLE_PLAN_KEY, require_header_title_plan, validate_header_title_plan,
)
from utils.hashing import canonical_json_digest
from utils.outline_utils import explicit_outline_title


_VERSION = "svg-common-header-title-batch/v1"
_NUMBERS = re.compile(r"[+-]?\d+(?:[.,]\d+)*(?:%|퍼센트)?")
_PROTECTED_SOURCE = re.compile(r"`([^`\n]+)`|\"([^\"\n]+)\"|[“‘]([^”’\n]+)[”’]")
_CLAIM_WORDS = re.compile(r"성과|달성|보장|확정|성공|개선|증가|감소|절감|때문|결과|"
                         r"\b(?:achieved|guaranteed|confirmed|improved|increased|decreased|because|therefore)\b", re.I)
_QUALIFICATIONS = re.compile(r"미달성|미확정|미보장|미완료|않|못|없|아니|예정|계획|추정|예상|잠정|가능|조건부|검토|"
                             r"\b(?:not|never|no|planned|possible|potential|estimated|expected|conditional|uncertain)\b", re.I)


class _TitleValidationError(ValueError):
    """Safe rejection location; never retain provider titles or source text."""

    def __init__(self, reason: str, *, page_index: int, validation_stage: str):
        super().__init__(reason)
        self.diagnostic = {"page_index": page_index, "validation_stage": validation_stage}


def header_title_pages(outline) -> tuple[object, list[dict], list[int]]:
    """Clone and reuse meaningful explicit titles without touching source text."""
    prepared = outline.model_copy(deep=True)
    pages, missing = [], []
    for index, slide in enumerate(prepared.slides):
        if slide.title is None or (isinstance(slide.title, str) and not slide.title.strip()):
            slide.title = explicit_outline_title(slide.content)
        if slide.title is not None and not isinstance(slide.title, str):
            raise ValueError("svg_common_header_title_invalid")
        if slide.title is None:
            missing.append(index)
        pages.append({"page_index": index, "content": slide.content,
                      "source_digest": common_header_source_digest(slide.content), "title": slide.title})
    if not pages:
        raise ValueError("svg_common_header_titles_missing")
    return prepared, pages, missing


def _validate_title(title: object, quote: object, source: str) -> tuple[str, int, int]:
    if (not isinstance(title, str) or not title.strip() or title != title.strip()
            or not any(character.isalpha() for character in title)
            or "\n" in title or "\r" in title
            or any(ord(character) < 32 or character in "\u200b\u200c\u200d\u2060\ufeff" for character in title)
            or not isinstance(quote, str) or not quote.strip() or quote not in source
            or explicit_outline_title("# " + title) != title):
        raise ValueError("svg_common_header_title_response_invalid")
    if any(number not in _NUMBERS.findall(quote) for number in _NUMBERS.findall(title)):
        raise ValueError("svg_common_header_title_source_mismatch")
    claims = list(_CLAIM_WORDS.finditer(title))
    if any(match.group(0).lower() not in quote.lower() for match in claims):
        raise ValueError("svg_common_header_title_source_mismatch")
    # This conservative guard prevents an observed possible/negative outcome
    # from becoming an affirmative title. It is a signal check, not a proof of
    # semantic entailment: the exact quote alone cannot establish that proof.
    if claims and any(match.group(0).lower() not in title.lower()
                      for match in _QUALIFICATIONS.finditer(source)):
        raise ValueError("svg_common_header_title_qualification_mismatch")
    protected = []
    for source_match in _PROTECTED_SOURCE.finditer(source):
        phrase = next(group for group in source_match.groups() if group is not None)
        start = title.find(phrase)
        if start >= 0:
            protected.append((start, start + len(phrase)))
    # Whole-text protection requires an exact source quotation. Other spans
    # must be non-overlapping and come from caller-owned source, never title
    # self-protection. Semantic numeric/unit/URL symbols use the existing scan.
    if (0, len(title)) in protected:
        scan = scan_visible_text(title, source_text=title)
    else:
        spans, end = [], 0
        for start, stop in sorted(set(protected)):
            if start >= end:
                spans.append(ProtectedSpan(start, stop, "source-quoted-value"))
                end = stop
        scan = scan_visible_text(title, protected_spans=spans)
    if scan.violations:
        raise ValueError("svg_common_header_title_writing_policy_invalid")
    start = source.index(quote)
    return title, start, start + len(quote)


def _validated_result(response: object, payload: dict) -> dict:
    if (not isinstance(response, dict) or set(response) != {"source_digest", "page_count", "titles"}
            or response["source_digest"] != payload["source_digest"]
            or type(response["page_count"]) is not int or response["page_count"] != payload["page_count"]
            or not isinstance(response["titles"], list) or len(response["titles"]) != len(payload["pages"])):
        raise ValueError("svg_common_header_title_response_invalid")
    pages = {page["page_index"]: page for page in payload["pages"]}
    titles, seen = [], set()
    for item in response["titles"]:
        if (not isinstance(item, dict) or set(item) != {"page_index", "source_digest", "title", "source_quote"}
                or type(item["page_index"]) is not int or item["page_index"] not in pages
                or item["page_index"] in seen):
            raise ValueError("svg_common_header_title_response_invalid")
        index = item["page_index"]
        page = pages[index]
        if item["source_digest"] != page["source_digest"]:
            raise _TitleValidationError("svg_common_header_title_source_mismatch",
                page_index=index, validation_stage="source_digest")
        try:
            title, start, end = _validate_title(item["title"], item["source_quote"], page["content"])
        except ValueError as error:
            raise _TitleValidationError(str(error), page_index=index,
                validation_stage="title_source_quote") from error
        titles.append({"page_index": index, "source_digest": page["source_digest"], "title": title,
                       "source_start": start, "source_end": end,
                       "source_quote_digest": common_header_source_digest(item["source_quote"])})
        seen.add(index)
    return {"source_digest": payload["source_digest"], "page_count": payload["page_count"],
            "titles": sorted(titles, key=lambda item: item["page_index"])}


def _restore_result(result: object, payload: dict) -> dict:
    if (not isinstance(result, dict) or set(result) != {"source_digest", "page_count", "titles"}
            or not isinstance(result["titles"], list)):
        raise ValueError("svg_common_header_title_checkpoint_corrupt")
    pages = {page["page_index"]: page for page in payload["pages"]}
    response = {**result, "titles": []}
    for item in result["titles"]:
        expected = {"page_index", "source_digest", "title", "source_start", "source_end", "source_quote_digest"}
        if (not isinstance(item, dict) or set(item) != expected or type(item["page_index"]) is not int
                or item["page_index"] not in pages or type(item["source_start"]) is not int
                or type(item["source_end"]) is not int):
            raise ValueError("svg_common_header_title_checkpoint_corrupt")
        content = pages[item["page_index"]]["content"]
        if not 0 <= item["source_start"] < item["source_end"] <= len(content):
            raise ValueError("svg_common_header_title_checkpoint_corrupt")
        quote = content[item["source_start"]:item["source_end"]]
        if common_header_source_digest(quote) != item["source_quote_digest"]:
            raise ValueError("svg_common_header_title_checkpoint_corrupt")
        response["titles"].append({"page_index": item["page_index"], "source_digest": item["source_digest"],
                                   "title": item["title"], "source_quote": quote})
    if _validated_result(response, payload) != result:
        raise ValueError("svg_common_header_title_checkpoint_corrupt")
    return result


async def _checkpoint(request, job, plan, *, session_maker, owned_job, terminal=None, reserve=True):
    async with session_maker() as session:
        current = await owned_job(session, job.id, job.attempt_token,
                                  fencing_token=getattr(job, "_workspace_fencing_token", None))
        admitted = validate_header_title_plan((current.request_payload or {}).get(INTERNAL_HEADER_TITLE_PLAN_KEY))
        if (admitted != plan or current.request_sha256 != job.request_sha256
                or current.attempt_number != job.attempt_number or current.attempt_token != job.attempt_token):
            raise ValueError("svg_common_header_title_lineage_conflict")
        identity = {"job_id": str(current.id), "request_digest": current.request_sha256,
                    "workspace_operation_id": str(current.workspace_operation_id) if current.workspace_operation_id else None,
                    "plan_digest": plan["plan_digest"]}
        digest = canonical_checkpoint_digest(identity)
        row = await session.scalar(select(AuthoredDeckPlanCheckpoint).where(
            AuthoredDeckPlanCheckpoint.generation_job_id == job.id, AuthoredDeckPlanCheckpoint.stage == "header"))
        if row is not None:
            stored = row.payload
            if (row.input_digest != digest or not isinstance(stored, dict)
                    or row.output_digest != canonical_checkpoint_digest(stored)
                    or stored.get("version") != _VERSION or stored.get("identity") != identity):
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            if terminal is None:
                if stored.get("state") != "succeeded":
                    raise ValueError("svg_common_header_title_outcome_unresolved")
                if set(stored) != {"version", "identity", "state", "result"} or not isinstance(stored["result"], dict):
                    raise ValueError("svg_common_header_title_checkpoint_corrupt")
                return stored["result"]
            if (stored.get("state") != "started" or set(stored) != {"version", "identity", "state"}
                    or row.produced_attempt_number != job.attempt_number):
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            updated = {**stored, **terminal}
            replaced = await session.execute(update(AuthoredDeckPlanCheckpoint).where(
                AuthoredDeckPlanCheckpoint.id == row.id,
                AuthoredDeckPlanCheckpoint.output_digest == row.output_digest,
                AuthoredDeckPlanCheckpoint.produced_attempt_number == job.attempt_number,
            ).values(payload=updated, output_digest=canonical_checkpoint_digest(updated)))
            if replaced.rowcount != 1:
                raise ValueError("svg_common_header_title_checkpoint_conflict")
            await session.commit()
            return None
        if terminal is not None:
            raise ValueError("svg_common_header_title_checkpoint_missing")
        if not reserve:
            return None
        stored = {"version": _VERSION, "identity": identity, "state": "started"}
        session.add(AuthoredDeckPlanCheckpoint(generation_job_id=job.id, stage="header", input_digest=digest,
            output_digest=canonical_checkpoint_digest(stored), payload=stored,
            produced_attempt_number=job.attempt_number))
        await session.commit()
        return None


async def prepare_missing_header_titles(request, job, outline, *, session_maker, owned_job,
                                       provider_callback: Callable[..., Awaitable[dict]] | None = None):
    """Return a title-enriched clone; default normal outline call count is zero."""
    if getattr(request, "common_header_mode", None) != "uniform":
        return outline
    prepared, pages, missing = header_title_pages(outline)
    if not missing:
        return prepared
    if not getattr(request, "slides_markdown", None):
        raise ValueError("svg_common_header_title_unresolved")
    plan = require_header_title_plan(request, pages, missing)
    if job is None or not getattr(job, "attempt_token", None) or type(job.attempt_number) is not int or job.attempt_number < 1:
        raise ValueError("svg_common_header_title_durable_job_required")
    payload = {"source_digest": plan["source_digest"], "page_count": len(pages),
               "pages": [{key: page[key] for key in ("page_index", "source_digest", "content")}
                         for page in pages if page["page_index"] in missing]}
    from utils.llm_calls.prepare_svg_common_header_titles import (
        build_svg_common_header_title_call, validate_header_title_capacity,
    )
    validate_header_title_capacity(payload, plan=plan)
    result = await _checkpoint(request, job, plan, session_maker=session_maker, owned_job=owned_job, reserve=False)
    if result is None:
        callback = provider_callback or build_svg_common_header_title_call(payload, plan=plan)
        result = await _checkpoint(request, job, plan, session_maker=session_maker, owned_job=owned_job)
    if result is not None:
        result = _restore_result(result, payload)
    else:
        try:
            with observe_setup(request, "title"), provider_usage_scope(
                    job_id=job.id, operation="generate", stage="outline",
                    attempt_number=job.attempt_number, public_only=bool(getattr(job, "public_only", False)),
                    workspace_operation_id=(getattr(job, "workspace_operation_id", None)
                        if (getattr(job, "request_payload", None) or {}).get("_svg_partial_recovery")
                        else None)):
                response = await callback(payload, plan=plan)
        except BaseException:
            # A failed persistence leaves the already committed started marker,
            # which remains non-replayable. Never reset it or hide ambiguity.
            await _checkpoint(request, job, plan, session_maker=session_maker, owned_job=owned_job,
                              terminal={"state": "unknown", "reason": "svg_common_header_title_provider_outcome_unknown"})
            raise
        try:
            result = _validated_result(response, payload)
        except ValueError as error:
            diagnostic = {"diagnostic": error.diagnostic} if isinstance(error, _TitleValidationError) else {}
            await _checkpoint(request, job, plan, session_maker=session_maker, owned_job=owned_job,
                              terminal={"state": "rejected", "reason": str(error), **diagnostic})
            raise
        await _checkpoint(request, job, plan, session_maker=session_maker, owned_job=owned_job,
                          terminal={"state": "succeeded", "result": result})
    for item in result["titles"]:
        prepared.slides[item["page_index"]].title = item["title"]
        prepared.slides[item["page_index"]].title_source_digest = canonical_json_digest(item)
    if any(before.content != after.content for before, after in zip(outline.slides, prepared.slides, strict=False)):
        raise ValueError("svg_common_header_title_source_mismatch")
    return prepared
