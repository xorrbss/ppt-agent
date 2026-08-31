"""Try the fixed local candidate set; terminal integrity errors never recover."""
from __future__ import annotations

from dataclasses import dataclass, replace
import inspect
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .candidates import Candidate
from .compiler import CompileResult
from .grammar import DeckIrGrammarError, digest

BrowserObserver = Callable[[CompileResult], Mapping[str, Any]]
Compiler = Callable[[Candidate, int, str | None], CompileResult]
AsyncCompiler = Callable[[Candidate, int, str | None], CompileResult | Awaitable[CompileResult]]
AsyncBrowserObserver = Callable[[CompileResult], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
_CANDIDATE_REJECTIONS = frozenset({"deck_ir_capacity_unsatisfiable", "deck_ir_payload_render_unsupported"})


@dataclass(frozen=True)
class BrowserClosureResult:
    result: CompileResult
    candidate: Candidate
    receipt: Mapping[str, Any]


def _trial_sequence(ranked_candidates, maximum_recompiles):
    """Each of K candidates gets one observation, not a hidden provider retry.

    The separate per-candidate recompile ceiling remains two; this resolver
    does not rewrite a candidate and therefore consumes zero recompiles.
    """
    if not 1 <= len(ranked_candidates) <= 8 or maximum_recompiles != 2:
        raise DeckIrGrammarError("browser_measurement_closure_failed")
    if len({item.candidate_id for item in ranked_candidates}) != len(ranked_candidates):
        raise DeckIrGrammarError("candidate_selection_receipt_invalid")
    attempts = []
    for ordinal, candidate in enumerate(ranked_candidates):
        attempt = {"candidateId": candidate.candidate_id, "candidateDigest": candidate.candidate_digest,
                   "resolutionReceiptDigest": candidate.resolution_receipt["receiptDigest"],
                   "candidateOrdinal": ordinal, "closureRevision": 0, "passed": False}
        try:
            result = yield ("compile", candidate)
        except DeckIrGrammarError as error:
            if error.code not in _CANDIDATE_REJECTIONS:
                raise
            attempts.append({**attempt, "reason": error.code})
            continue
        if not isinstance(result, CompileResult):
            raise DeckIrGrammarError("constraint_compile_failed")
        observation = yield ("observe", result)
        if not isinstance(observation, Mapping):
            raise DeckIrGrammarError("browser_observation_failed")
        observation_digest = digest(observation)
        passed = observation.get("passed") is True
        attempts.append({**attempt, "observationDigest": observation_digest, "passed": passed,
                         "failedChecks": [str(key) for key, value in observation.get("checks", {}).items() if value is not True],
                         "reason": "passed" if passed else "browser_constraints_rejected"})
        if passed:
            unsigned = {key: value for key, value in result.receipt.items() if key != "receiptDigest"}
            unsigned.update({"measurementClosureRevision": 0, "browserMeasurementDigest": observation_digest})
            result = replace(result, receipt={**unsigned, "receiptDigest": digest(unsigned)})
            return BrowserClosureResult(result, candidate, _receipt(attempts, len(ranked_candidates), candidate.candidate_id))
    error = DeckIrGrammarError("browser_measurement_closure_failed")
    error.receipt = _receipt(attempts, len(ranked_candidates), None)
    raise error


def _receipt(attempts, count, selected):
    receipt = {"schemaVersion": "browser-measurement-closure-receipt/v2", "selectedCandidateId": selected,
               "maximumCandidateAttempts": count, "candidateAttemptCount": len(attempts),
               "maximumRecompiles": 2, "recompileCount": 0, "providerCallsAdded": 0,
               "attempts": attempts, "status": "passed" if selected else "exhausted"}
    return {**receipt, "receiptDigest": digest(receipt)}


def close_ranked_candidates(*, ranked_candidates: Sequence[Candidate], compile_candidate: Compiler,
                            observe: BrowserObserver, maximum_recompiles: int = 2) -> BrowserClosureResult:
    trials = _trial_sequence(ranked_candidates, maximum_recompiles)
    action, value = next(trials)
    while True:
        try:
            response = compile_candidate(value, 0, None) if action == "compile" else observe(value)
        except DeckIrGrammarError as error:
            action, value = trials.throw(error)
            continue
        try:
            action, value = trials.send(response)
        except StopIteration as done:
            return done.value


async def async_close_ranked_candidates(*, ranked_candidates: Sequence[Candidate], compile_candidate: AsyncCompiler,
                                       observe: AsyncBrowserObserver, maximum_recompiles: int = 2) -> BrowserClosureResult:
    trials = _trial_sequence(ranked_candidates, maximum_recompiles)
    action, value = next(trials)
    while True:
        try:
            response = compile_candidate(value, 0, None) if action == "compile" else observe(value)
            if inspect.isawaitable(response):
                response = await response
        except DeckIrGrammarError as error:
            action, value = trials.throw(error)
            continue
        try:
            action, value = trials.send(response)
        except StopIteration as done:
            return done.value
