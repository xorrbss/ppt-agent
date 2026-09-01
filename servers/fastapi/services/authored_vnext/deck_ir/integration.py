"""Fenced Deck IR execution seam for the public generation worker.

The seam is deliberately dependency-injected: WP1/WP2 may arrive in a rolling
deployment and publication remains owned by the existing SQL/CAS path.  An
unconfigured Deck IR operation fails closed; it never falls back to provider
HTML, generic templates, or a raster-only result.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import time
from typing import Any, Protocol

from services.authored_checkpoint_artifacts import write_detached_deck_ir_checkpoint, write_deck_ir_execution_evidence
from services.authored_vnext.deck_ir.director import (
    DeckDirectorAdapter,
    DeckDirectorAdapterUnavailable,
    run_balanced_deck_director,
)
from services.authored_vnext.deck_ir.strategy import (
    GenerationStrategyCapabilityError,
    validate_sealed_generation_strategy,
)


logger = logging.getLogger(__name__)


class DeckIrIntegrationError(RuntimeError):
    code = "deck_ir_execution_adapter_unavailable"
    retryable = False

    def __init__(
        self, code: str | None = None, *, quality_receipt: Mapping[str, object] | None = None
    ) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved
        # Private, checker-produced evidence only. Public errors retain the
        # stable reason code; raw input and local paths never enter this receipt.
        self.quality_receipt = dict(quality_receipt) if quality_receipt is not None else None


class DeckIrExecutionAdapter(Protocol):
    async def run(
        self,
        *,
        request: object,
        generation_job: object,
        context: object,
        strategy_receipt: Mapping[str, object],
    ) -> Mapping[str, object]: ...


_configured_execution_adapter: DeckIrExecutionAdapter | None = None


def configure_deck_ir_execution_adapter(adapter: DeckIrExecutionAdapter | None) -> None:
    """Install the process-local production bridge during application startup.

    Configuration is intentionally explicit.  A missing bridge is a stable
    fail-closed error, never permission to route a sealed Deck IR operation to
    legacy provider HTML.
    """

    global _configured_execution_adapter
    _configured_execution_adapter = adapter


def deck_ir_execution_adapter_configured() -> bool:
    return _configured_execution_adapter is not None


def ensure_product_deck_ir_execution_adapter(*, explicit: bool = False) -> bool:
    """Install the real composition root for an explicitly opted-in process.

    Construction performs no provider request.  Admission still seals strategy
    before the adapter can reserve a provider call.
    """

    if not explicit and not os.getenv("DECK_IR_GENERATION_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return False
    if _configured_execution_adapter is None:
        from services.authored_vnext.deck_ir.product_adapter import (
            build_product_deck_ir_execution_adapter,
        )

        configure_deck_ir_execution_adapter(build_product_deck_ir_execution_adapter())
    return True


def _digest(payload: Mapping[str, object]) -> str:
    try:
        from services.authored_vnext.deck_ir.canonical import canonical_digest

        value = canonical_digest(payload)
        if isinstance(value, str) and value:
            return value
    except (ImportError, TypeError, ValueError):
        pass
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _progress(context: object, *, stage: str, percent: int, message: str) -> None:
    await getattr(context, "raise_if_cancelled")()
    accepted = await getattr(context, "progress")(
        stage=stage, percent=percent, message=message
    )
    if accepted is False:
        raise DeckIrIntegrationError("deck_ir_operation_lease_lost")


def _as_mapping(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DeckIrIntegrationError(code)
    return dict(value)


def _record_stage_timing(request: object, stage: str, started_at: float) -> None:
    recorder = getattr(request, "_authored_generation_timing_recorder", None)
    record = getattr(recorder, "record_stage_ms", None)
    if callable(record):
        record(stage, max(0, round((time.monotonic() - started_at) * 1000)))


def _deck_ir_from_director(run: Mapping[str, object]) -> dict[str, object]:
    """Accept only structured Deck IR; never reinterpret raw provider strings."""

    direct = run.get("deckIr")
    if isinstance(direct, Mapping):
        return dict(direct)
    global_plan = run.get("globalPlan")
    if isinstance(global_plan, Mapping) and isinstance(global_plan.get("deckIr"), Mapping):
        return dict(global_plan["deckIr"])
    # Some adapters return global deck metadata plus batches.  Merge only the
    # declared slide objects; any absent deck metadata remains a validation
    # failure rather than a server-invented plan.
    if isinstance(global_plan, Mapping):
        slides: list[object] = []
        for batch in run.get("slideIrBatches", ()):
            if isinstance(batch, Mapping) and isinstance(batch.get("slides"), list):
                slides.extend(batch["slides"])
        candidate = dict(global_plan)
        story = global_plan.get("story")
        arc = story.get("arc") if isinstance(story, Mapping) else None
        candidate["slides"] = (
            [
                {**dict(slide), "role": arc[index]}
                if isinstance(slide, Mapping)
                else slide
                for index, slide in enumerate(slides)
            ]
            if isinstance(arc, list) and len(arc) == len(slides)
            else slides
        )
        # Global-plan envelopes deliberately omit only the repeated slide list;
        # normalizing that one wrapper field is not server invention of content.
        if candidate.get("schemaVersion") == "deck-global-plan/v1":
            candidate["schemaVersion"] = "deck-ir/v1"
        if candidate.get("schemaVersion") == "deck-ir/v1":
            return candidate
    raise DeckIrIntegrationError("deck_director_output_invalid")


def _intent_documents(run: Mapping[str, object]) -> tuple[list[object], list[object]]:
    """Read the detached composition/art-direction documents, never invent them."""

    global_plan = run.get("globalPlan")
    sources = (run, global_plan if isinstance(global_plan, Mapping) else {})
    for source in sources:
        compositions = source.get("compositionIntentGraphs")
        art_directions = source.get("artDirectionIntents")
        if isinstance(compositions, list) and isinstance(art_directions, list):
            return compositions, art_directions
    compositions: list[object] = []
    art_directions: list[object] = []
    for batch in run.get("slideIrBatches", ()):
        if not isinstance(batch, Mapping):
            continue
        raw_compositions = batch.get("compositionIntentGraphs")
        raw_art_directions = batch.get("artDirectionIntents")
        if not isinstance(raw_compositions, list) or not isinstance(raw_art_directions, list):
            raise DeckIrIntegrationError("deck_ir_slide_intent_incomplete")
        compositions.extend(raw_compositions)
        art_directions.extend(raw_art_directions)
    if compositions or art_directions:
        return compositions, art_directions
    raise DeckIrIntegrationError("deck_ir_slide_intent_incomplete")


def _validate_deck_ir(
    deck_ir: Mapping[str, object], *, compositions: list[object], art_directions: list[object],
    slide_count: int, requirement_graph: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], dict[str, object] | None]:
    """Use WP1 validation when present; no permissive schema fallback exists."""

    try:
        from services.authored_vnext.deck_ir.contracts import (
            ArtDirectionIntentV1,
            CompositionIntentGraphV1,
            DeckIRV1,
            RequirementCoverageReceiptV1,
            RequirementGraphV1,
        )
        from services.authored_vnext.deck_ir.canonical import canonical_digest, seal_contract
        from services.authored_vnext.deck_ir.validation import (
            validate_deck_ir, validate_requirement_coverage, requirement_coverage_totals,
        )

        sealed_compositions = []
        for item in compositions:
            raw = _as_mapping(item, code="composition_intent_graph_invalid")
            if "graphDigest" in raw:
                raise DeckIrIntegrationError("composition_intent_graph_model_digest_forbidden")
            sealed_compositions.append(seal_contract(raw, model_type=CompositionIntentGraphV1))
        compositions_by_slide = {item.slide_key: item for item in sealed_compositions}
        sealed_art_directions = []
        for item in art_directions:
            raw = _as_mapping(item, code="art_direction_intent_invalid")
            key = raw.get("slideKey")
            if "intentDigest" in raw or "compositionIntentGraphDigest" in raw or not isinstance(key, str):
                raise DeckIrIntegrationError("art_direction_model_digest_forbidden")
            composition = compositions_by_slide.get(key)
            if composition is None:
                raise DeckIrIntegrationError("deck_ir_slide_intent_incomplete")
            sealed_art_directions.append(seal_contract(
                {**raw, "compositionIntentGraphDigest": composition.graph_digest},
                model_type=ArtDirectionIntentV1,
            ))
        art_by_slide = {item.slide_key: item for item in sealed_art_directions}
        raw_deck = dict(deck_ir)
        graph = (
            RequirementGraphV1.model_validate(requirement_graph)
            if requirement_graph is not None
            else None
        )
        allowed_requirement_ids = (
            {item.requirement_id for item in graph.requirements}
            if graph is not None else None
        )
        normalized_slides = []
        for item in raw_deck.get("slides", []):
            raw = _as_mapping(item, code="deck_director_output_invalid")
            key = raw.get("slideKey")
            if (
                "compositionIntentGraphDigest" in raw
                or "artDirectionIntentDigest" in raw
                or not isinstance(key, str)
                or key not in compositions_by_slide
                or key not in art_by_slide
            ):
                raise DeckIrIntegrationError("deck_ir_model_digest_forbidden")
            # The public adapter is source-free.  Do not let the model invent
            # source identity or a source content digest merely to satisfy a
            # schema; any real source binding is admitted by a later adapter.
            if raw.get("sourceBindingIds"):
                raise DeckIrIntegrationError("deck_ir_source_binding_model_forbidden")
            if allowed_requirement_ids is not None and not set(raw.get("requirementIds", [])).issubset(allowed_requirement_ids):
                raise DeckIrIntegrationError("deck_ir_requirement_identity_model_forbidden")
            for component in raw.get("components", []):
                component_raw = _as_mapping(component, code="deck_director_output_invalid")
                payload = component_raw.get("payload")
                if component_raw.get("sourceBindingId") or (
                    isinstance(payload, Mapping) and payload.get("sourceContentDigest")
                ):
                    raise DeckIrIntegrationError("deck_ir_source_binding_model_forbidden")
                if allowed_requirement_ids is not None and not set(component_raw.get("requirementIds", [])).issubset(allowed_requirement_ids):
                    raise DeckIrIntegrationError("deck_ir_requirement_identity_model_forbidden")
            normalized_slides.append({
                **raw,
                "compositionIntentGraphDigest": compositions_by_slide[key].graph_digest,
                "artDirectionIntentDigest": art_by_slide[key].intent_digest,
            })
        raw_deck["slides"] = normalized_slides
        validated = DeckIRV1.model_validate(raw_deck)
        validate_deck_ir(
            validated,
            sealed_compositions,
            sealed_art_directions,
            requirement_graph=graph,
            admitted_slide_count=slide_count,
            provider_art_proposals=True,
        )
        coverage: dict[str, object] | None = None
        if graph is not None:
            occurrences = [
                {"requirementId": requirement_id, "slideKey": slide.slide_key, "elementKey": component.element_key}
                for slide in validated.slides
                for component in slide.components
                for requirement_id in component.requirement_ids
            ]
            unsigned = {
                "schemaVersion": "requirement-coverage-receipt/v1",
                "requirementGraphDigest": graph.graph_digest,
                "deckIrDigest": canonical_digest(validated),
                **requirement_coverage_totals(graph, occurrences),
            }
            receipt = seal_contract(unsigned, model_type=RequirementCoverageReceiptV1)
            validate_requirement_coverage(receipt, graph, validated)
            if receipt.status != "passed":
                raise DeckIrIntegrationError("deck_ir_requirement_coverage_failed")
            coverage = dict(receipt.model_dump(mode="json", by_alias=True))
        return (
            dict(validated.model_dump(mode="json", by_alias=True)),
            [dict(item.model_dump(mode="json", by_alias=True)) for item in sealed_compositions],
            [dict(item.model_dump(mode="json", by_alias=True)) for item in sealed_art_directions],
            coverage,
        )
    except ImportError as error:
        raise DeckIrIntegrationError("deck_ir_contract_runtime_unavailable") from error
    except (TypeError, ValueError) as error:
        stable_code = getattr(error, "code", None)
        raw_errors = getattr(error, "errors", None)
        diagnostics: list[dict[str, str]] = []
        if callable(raw_errors):
            for item in raw_errors()[:20]:
                if not isinstance(item, Mapping):
                    continue
                location = item.get("loc")
                diagnostics.append(
                    {
                        "loc": ".".join(str(part) for part in location)
                        if isinstance(location, (list, tuple))
                        else "unknown",
                        "type": str(item.get("type") or "validation_error")[:96],
                        "message": str(item.get("msg") or "validation failed")[:160],
                    }
                )
        logger.warning(
            "Deck IR contract validation rejected provider output diagnostics=%s",
            diagnostics
            or [{
                "loc": "cross-contract",
                "type": str(stable_code or type(error).__name__)[:96],
                "rule": getattr(error, "rule", None) or "unspecified",
            }],
        )
        raise DeckIrIntegrationError(
            stable_code if isinstance(stable_code, str) else "deck_director_output_invalid"
        ) from error


@dataclass
class ConstraintCompilerDeckIrExecutionAdapter:
    """Production bridge: structured director -> compiler -> quality -> CAS publisher.

    `publisher` is the existing manifest CAS owner.  The bridge never writes a
    manifest itself and it intentionally has no ArtifactQualityReceipt hook:
    artifact/export verification is a later operation with separate authority.
    """

    director: DeckDirectorAdapter
    grammar: object
    reserve_provider: Callable[[str], Awaitable[None]]
    observe: Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
    generation_quality: Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
    publisher: Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
    requirement_graph: Mapping[str, object] | None = None
    language: str | None = None
    authored_style: str | None = None
    style_profile: object | None = None

    async def run(
        self,
        *,
        request: object,
        generation_job: object,
        context: object,
        strategy_receipt: Mapping[str, object],
    ) -> Mapping[str, object]:
        operation_id = str(getattr(getattr(context, "claim"), "operation_id"))
        strategy = validate_sealed_generation_strategy(
            strategy_receipt, operation_id=operation_id
        )
        if strategy != "deck-ir":
            raise GenerationStrategyCapabilityError("deck_ir_strategy_not_selected")
        await _progress(context, stage="authoring", percent=10, message="Planning deck")
        slide_count = getattr(request, "n_slides", None)
        if not isinstance(slide_count, int) or isinstance(slide_count, bool):
            raise DeckIrIntegrationError("deck_ir_slide_count_invalid")
        outline_started_at = time.monotonic()
        director_run = await run_balanced_deck_director(
            adapter=self.director,
            brief=str(getattr(request, "content", "") or ""),
            slide_count=slide_count,
            language=self.language or getattr(request, "language", None),
            strategy_receipt=strategy_receipt,
            reserve=self.reserve_provider,
            requirement_graph=self.requirement_graph,
        )
        _record_stage_timing(request, "outline", outline_started_at)
        author_started_at = time.monotonic()
        compositions, art_directions = _intent_documents(director_run)
        raw_deck_ir = _deck_ir_from_director(director_run)
        style_profile_digest = None
        retained_reference_digest = None
        if self.style_profile is not None:
            from services.authored_vnext.deck_ir.style_profiles import (
                DeckIrStyleProfile,
                apply_server_style_profile,
            )

            if not isinstance(self.style_profile, DeckIrStyleProfile):
                raise DeckIrIntegrationError("deck_ir_style_profile_unsupported")
            style_profile_digest = self.style_profile.profile_digest
            retained_reference_digest = self.style_profile.retained_reference_digest
            if strategy_receipt.get("styleProfileDigest") != style_profile_digest:
                raise DeckIrIntegrationError("deck_ir_style_profile_binding_invalid")
            try:
                raw_deck_ir, art_directions = apply_server_style_profile(
                    deck_ir=raw_deck_ir,
                    art_directions=art_directions,
                    profile=self.style_profile,
                )
            except ValueError as error:
                raise DeckIrIntegrationError(str(error)) from error
        deck_ir, compositions, art_directions, coverage = _validate_deck_ir(
            raw_deck_ir,
            compositions=compositions,
            art_directions=art_directions,
            slide_count=slide_count,
            requirement_graph=self.requirement_graph,
        )
        await _progress(context, stage="authoring", percent=45, message="Deck IR validated")
        checkpoint = write_detached_deck_ir_checkpoint(
            job_id=getattr(generation_job, "id"),
            attempt_token=getattr(getattr(context, "claim"), "attempt_token"),
            fencing_token=getattr(getattr(context, "claim"), "fencing_token"),
            payload={
                "operationId": operation_id,
                "strategyReceiptDigest": strategy_receipt.get("receiptDigest"),
                "styleProfileDigest": style_profile_digest,
                "retainedReferenceDigest": retained_reference_digest,
                "deckIr": deck_ir,
                "directorRun": director_run,
                "requirementCoverageReceipt": coverage,
            },
        )
        await _progress(context, stage="validating", percent=55, message="Selecting candidates")
        try:
            from services.authored_vnext.deck_ir.candidates import (
                enumerate_candidates,
                select_deck_candidates,
            )
            from services.authored_vnext.deck_ir.compiler import compile_slide
            from services.authored_vnext.deck_ir.browser_closure import (
                async_close_ranked_candidates,
            )
            from services.authored_vnext.deck_ir.grammar import validate_manifest
        except ImportError as error:
            raise DeckIrIntegrationError("deck_ir_compiler_runtime_unavailable") from error
        slides = deck_ir.get("slides")
        if not isinstance(slides, list) or len(slides) != slide_count:
            raise DeckIrIntegrationError("deck_ir_request_mismatch")
        grammar = validate_manifest(self.grammar)
        compositions_by_slide = {
            item.get("slideKey"): item for item in compositions if isinstance(item, Mapping)
        }
        art_directions_by_slide = {
            item.get("slideKey"): item for item in art_directions if isinstance(item, Mapping)
        }
        candidates_by_slide: dict[str, object] = {}
        for slide in slides:
            if not isinstance(slide, Mapping):
                raise DeckIrIntegrationError("deck_director_output_invalid")
            key = slide.get("slideKey")
            if not isinstance(key, str) or not key:
                raise DeckIrIntegrationError("deck_director_output_invalid")
            composition = compositions_by_slide.get(key)
            art_direction = art_directions_by_slide.get(key)
            if not isinstance(composition, Mapping) or not isinstance(art_direction, Mapping):
                raise DeckIrIntegrationError("deck_ir_slide_intent_incomplete")
            candidates_by_slide[key] = enumerate_candidates(
                slide=slide,
                composition=composition,
                art_direction=art_direction,
                grammar=grammar,
            )
        selection = select_deck_candidates(candidates_by_slide=candidates_by_slide, grammar=grammar)
        selection_receipt = _as_mapping(
            getattr(selection, "receipt", selection), code="candidate_selection_receipt_invalid"
        )
        selected = getattr(selection, "selected", None)
        ranked = getattr(selection, "ranked", None)
        if not isinstance(selected, Mapping) or not isinstance(ranked, Mapping):
            raise DeckIrIntegrationError("candidate_selection_receipt_invalid")
        compiled: list[dict[str, object]] = []
        actual_candidates = {}

        def retain_execution(payload):
            return write_deck_ir_execution_evidence(
                job_id=getattr(generation_job, "id"),
                attempt_token=getattr(getattr(context, "claim"), "attempt_token"),
                fencing_token=getattr(getattr(context, "claim"), "fencing_token"),
                payload={"operationId": operation_id, "originalCheckpointDigest": checkpoint["checkpointDigest"],
                         "candidateSelectionReceipt": selection_receipt, **payload},
            )
        observations_by_slide: dict[str, dict[str, object]] = {}
        render_elapsed_ms = 0
        for slide in slides:
            key = str(slide["slideKey"])
            candidate = selected.get(key)
            ranked_candidates = ranked.get(key)
            if candidate is None or not isinstance(ranked_candidates, tuple):
                raise DeckIrIntegrationError("candidate_set_exhausted")
            def compile_candidate(item: object, closure_revision: int, measurement_digest: str | None) -> object:
                return compile_slide(
                    operation_id=operation_id,
                    deck_ir=deck_ir,
                    slide=slide,
                    grammar=grammar,
                    candidate=item,
                    selection_receipt=selection_receipt,
                    closure_revision=closure_revision,
                    browser_measurement_digest=measurement_digest,
                    strategy_decision_digest=str(strategy_receipt.get("receiptDigest") or ""),
                    authored_style=self.authored_style,
                    style_profile_digest=style_profile_digest,
                )

            async def observe_candidate(result: object) -> Mapping[str, object]:
                candidate_html = getattr(result, "html", None)
                candidate_semantic = getattr(result, "semantic", None)
                candidate_receipt = getattr(result, "receipt", None)
                observed = _as_mapping(
                    await self.observe(
                        {
                            "slideKey": key,
                            "html": candidate_html,
                            "semantic": candidate_semantic,
                            "compileReceipt": candidate_receipt,
                        }
                    ),
                    code="browser_observation_failed",
                )
                observations_by_slide[key] = observed
                return observed

            render_started_at = time.monotonic()
            try:
                closure = await async_close_ranked_candidates(
                    ranked_candidates=ranked_candidates,
                    compile_candidate=compile_candidate,
                    observe=observe_candidate,
                    maximum_recompiles=2,
                )
            except ValueError as error:
                failure_receipt = getattr(error, "receipt", None)
                if isinstance(failure_receipt, Mapping):
                    retain_execution({"status": "exhausted", "slideKey": key,
                                      "browserClosureReceipt": dict(failure_receipt),
                                      "completedSlideReceipts": [item["compileReceipt"] for item in compiled]})
                raise
            actual_candidates[key] = closure.candidate
            render_elapsed_ms += max(0, round((time.monotonic() - render_started_at) * 1000))
            result = closure.result
            html = getattr(result, "html", None)
            semantic = getattr(result, "semantic", None)
            receipt = getattr(result, "receipt", None)
            if not isinstance(html, str) or not isinstance(semantic, Mapping) or not isinstance(receipt, Mapping):
                raise DeckIrIntegrationError("constraint_compile_failed")
            compiled.append({
                "slideKey": key,
                "html": html,
                "semantic": dict(semantic),
                "compileReceipt": dict(receipt),
                "browserClosureReceipt": dict(closure.receipt),
            })
        initial_selection_digest = selection_receipt["receiptDigest"]
        initial_selection_receipt = selection_receipt
        final_selection = {name: value for name, value in selection_receipt.items() if name != "receiptDigest"}
        final_selection["initialSelectionReceiptDigest"] = initial_selection_digest
        final_selection["slides"] = [{**entry, "selectedCandidateId": actual_candidates[entry["slideKey"]].candidate_id}
                                     for entry in selection_receipt["slides"]]
        final_selection["deckSelection"] = {**selection_receipt["deckSelection"],
            "selectedCombinationDigest": _digest([actual_candidates[key].candidate_digest for key in sorted(actual_candidates)])}
        selection_receipt = {**final_selection, "receiptDigest": _digest(final_selection)}
        for item in compiled:
            final_compile = {name: value for name, value in item["compileReceipt"].items() if name != "receiptDigest"}
            final_compile.update({"initialCandidateSelectionReceiptDigest": initial_selection_digest,
                                  "candidateSelectionReceiptDigest": selection_receipt["receiptDigest"]})
            item["compileReceipt"] = {**final_compile, "receiptDigest": _digest(final_compile)}
        execution_evidence = {"status": "passed", "candidateSelectionReceipt": selection_receipt,
                              "initialCandidateSelectionReceipt": initial_selection_receipt,
                              "slides": [{"compileReceipt": item["compileReceipt"], "browserClosureReceipt": item["browserClosureReceipt"]} for item in compiled]}
        retained_execution = retain_execution(execution_evidence)
        _record_stage_timing(request, "author", author_started_at)
        recorder = getattr(request, "_authored_generation_timing_recorder", None)
        record = getattr(recorder, "record_stage_ms", None)
        if callable(record):
            record("render", render_elapsed_ms)
        await _progress(context, stage="rendering", percent=72, message="Measuring compiled slides")
        observations = [observations_by_slide[str(slide["slideKey"])] for slide in compiled]
        await _progress(context, stage="validating", percent=85, message="Checking generation quality")
        quality_input = {
            "schemaVersion": "generation-quality-input/deck-ir-v1",
            "operationId": operation_id,
            "strategyReceiptDigest": strategy_receipt.get("receiptDigest"),
            "styleProfileDigest": style_profile_digest,
            "deckIrDigest": _digest(deck_ir),
            "deckIr": deck_ir,
            "checkpointDigest": checkpoint["checkpointDigest"],
            "candidateSelectionReceipt": selection_receipt,
            "requirementCoverageReceipt": coverage,
            "compiledSlides": compiled,
            "observations": observations,
        }
        quality_started_at = time.monotonic()
        try:
            generation_quality = _as_mapping(
                await self.generation_quality(quality_input), code="generation_quality_receipt_invalid"
            )
        except DeckIrIntegrationError as error:
            if error.quality_receipt is not None:
                retain_execution({
                    **execution_evidence,
                    "status": "quality_failed",
                    "generationQualityReceipt": error.quality_receipt,
                })
            raise
        finally:
            _record_stage_timing(request, "quality", quality_started_at)
        if generation_quality.get("schemaVersion") not in {
            "generation-quality-receipt/v1", "generation-quality-receipt/v3"
        }:
            raise DeckIrIntegrationError("generation_quality_receipt_invalid")
        execution_evidence = {**execution_evidence, "generationQualityReceipt": generation_quality}
        retained_execution = retain_execution(execution_evidence)
        await _progress(context, stage="packaging", percent=92, message="Staging immutable bundle")
        publication_started_at = time.monotonic()
        publication = _as_mapping(
            await self.publisher(
                {
                    "schemaVersion": "authored-emission-bundle-input/deck-ir-v1",
                    "operationId": operation_id,
                    "attemptToken": getattr(getattr(context, "claim"), "attempt_token"),
                    "fencingToken": getattr(getattr(context, "claim"), "fencing_token"),
                    "strategyReceipt": dict(strategy_receipt),
                    "styleProfileDigest": style_profile_digest,
                    "retainedReferenceDigest": retained_reference_digest,
                    "deckIr": deck_ir,
                    "compiledSlides": compiled,
                    "executionEvidence": execution_evidence,
                    "generationQualityReceipt": generation_quality,
                    "requirementCoverageReceipt": coverage,
                }
            ),
            code="authored_manifest_publication_failed",
        )
        _record_stage_timing(request, "publication", publication_started_at)
        if not isinstance(publication.get("manifestDigest"), str):
            raise DeckIrIntegrationError("authored_manifest_publication_failed")
        await _progress(context, stage="publishing", percent=100, message="Published")
        generation_deck_ir = {
                "strategy": "deck-ir",
                "strategyReceiptDigest": strategy_receipt.get("receiptDigest"),
                "deckIrDigest": _digest(deck_ir),
                "detachedCheckpointDigest": checkpoint["checkpointDigest"],
                "providerLogicalCalls": director_run["logicalCalls"],
                "providerOutputMode": "structured-deck-ir-json",
                "providerHtmlAuthoringCalls": 0,
                "legacyProviderHtmlExecuted": False,
                "candidateSearchK": 6,
                "candidateProviderCallsAdded": 0,
                "grammarDigest": grammar.manifest_digest,
                "styleProfileDigest": style_profile_digest,
                "retainedReferenceDigest": retained_reference_digest,
                "compilerVersion": "constraint-compiler/v1",
                "candidateSelectionReceiptDigest": selection_receipt.get("receiptDigest"),
                "executionEvidenceDigest": retained_execution["evidenceDigest"],
                "browserClosureReceiptDigests": [
                    item["browserClosureReceipt"].get("receiptDigest")
                    for item in compiled
                    if isinstance(item.get("browserClosureReceipt"), Mapping)
                ],
                "compileReceiptDigests": [
                    item["compileReceipt"].get("receiptDigest")
                    for item in compiled
                    if isinstance(item.get("compileReceipt"), Mapping)
                ],
                "slideCount": len(compiled),
            }
        return {
            "generationDeckIr": generation_deck_ir,
            "deckIr": generation_deck_ir,
            "generationQualityReceipt": generation_quality,
            "requirementCoverageReceipt": coverage,
            "publication": publication,
            # Artifact quality is intentionally absent: it is owned by the
            # subsequent export/readback operation, not generation success.
            "artifactQualityReceipt": None,
        }


async def run_deck_ir_generation(
    *,
    request: object,
    generation_job: object,
    context: object,
    strategy_receipt: Mapping[str, object],
    adapter: DeckIrExecutionAdapter | None = None,
) -> Mapping[str, object]:
    """Invoke only a configured Deck IR execution adapter; otherwise fail closed."""

    adapter = adapter or _configured_execution_adapter
    if adapter is None:
        raise DeckDirectorAdapterUnavailable("deck_ir_execution_adapter_unavailable")
    return await adapter.run(
        request=request,
        generation_job=generation_job,
        context=context,
        strategy_receipt=strategy_receipt,
    )


__all__ = [
    "ConstraintCompilerDeckIrExecutionAdapter",
    "configure_deck_ir_execution_adapter",
    "deck_ir_execution_adapter_configured",
    "ensure_product_deck_ir_execution_adapter",
    "DeckIrExecutionAdapter",
    "DeckIrIntegrationError",
    "run_deck_ir_generation",
]
