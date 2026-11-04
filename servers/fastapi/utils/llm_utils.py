import asyncio
import json
import logging
import re
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException
from llmai.shared import (
    LLMTool,
    Message,
    ReasoningConfig,
    ResponseFormat,
    ResponseStreamCompletionChunk,
    UserMessage,
)

from utils.llm_config import get_extra_body
from utils.llm_content_extraction import (
    extract_structured_content,
    # Re-exported: content coercion moved to `llm_content_extraction`, but
    # endpoints and llm_calls import both of these from here and that import
    # path is part of their contract.
    extract_text,  # noqa: F401
    message_content_to_text,  # noqa: F401
    serialize_structured_content,  # noqa: F401
)
from utils.schema_utils import get_schema_validation_errors
from utils.structured_semantic_slots import structured_semantic_slot
from services.provider_usage_instrumentation import (
    ProviderCallTimer,
    current_provider_idempotency_key,
    provider_call_admission,
    record_provider_call,
)
from utils.provider_idempotency import (
    provider_generate_call,
    supports_provider_idempotency,
)
from utils.provider_overload import provider_status_code
from utils.structured_call_budget import (
    PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS,  # noqa: F401  (re-exported)
    StructuredProviderCallBudget,
)
LOGGER = logging.getLogger(__name__)
CLIENT_DISCONNECT_POLL_SECONDS = 0.1
TRANSIENT_LLM_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_LLM_MAX_ATTEMPTS = 2
TRANSIENT_LLM_RETRY_BASE_DELAY_SECONDS = 1.0
TRANSIENT_LLM_RETRY_MAX_DELAY_SECONDS = 8.0
#: How long a cancelled structured call waits for its worker thread to observe
#: the transport abort before giving up on confirming it (see
#: ``_await_provider_call``).
PROVIDER_CALL_ABORT_GRACE_SECONDS = 30.0
PUBLIC_STRUCTURED_MAX_PARSE_ATTEMPTS = 2
PUBLIC_STRUCTURED_MAX_VALIDATION_LOOPS = 2
DisconnectChecker = Callable[[], Awaitable[bool]]
TextChunkCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class _StructuredGenerationResult:
    content: Optional[dict]
    response: object | None


#: Kept under its historical private name for the helpers below.
_StructuredProviderCallBudget = StructuredProviderCallBudget


async def _await_provider_call(generate_call: Callable[[], Any]) -> Any:
    """Run one blocking SDK call in a thread without leaving it behind on cancel.

    A thread cannot be interrupted, so ``asyncio.to_thread`` alone leaves a
    cancelled call -- the brief's stage deadline in ``asyncio.wait_for`` --
    running to completion: it keeps spending provider tokens, the retry runs
    concurrently with it and the orphan holds a default-executor slot.  The
    transport bounds the call instead (``provider_generate_call``: the SDK
    request timeout is the caller's deadline, and a streaming transport is
    told to abort at its next event).  On cancellation this asks for that
    abort and then waits for the worker to actually finish, so the caller's
    ``wait_for`` does not return -- and no retry starts -- while the call is
    still in flight.  The wait is bounded by
    ``PROVIDER_CALL_ABORT_GRACE_SECONDS``: a transport that cannot honour the
    abort is logged rather than waited on forever.
    """

    worker = asyncio.ensure_future(asyncio.to_thread(generate_call))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        abort = getattr(generate_call, "abort", None)
        if callable(abort):
            abort()
        if not worker.done():
            done, _pending = await asyncio.wait(
                {worker}, timeout=PROVIDER_CALL_ABORT_GRACE_SECONDS
            )
            if not done:
                LOGGER.warning(
                    "provider_call_abort_unconfirmed grace_seconds=%s: the "
                    "transport did not end the cancelled call in time",
                    PROVIDER_CALL_ABORT_GRACE_SECONDS,
                )
        if worker.done() and not worker.cancelled():
            worker.exception()  # observed: the result belongs to nobody now
        raise


def _provider_receipt_identity() -> str:
    """Resolve an allowlisted provider label without exposing call payloads."""

    try:
        from utils.llm_provider import get_llm_provider

        return str(get_llm_provider().value)
    except Exception:
        return "unknown"


async def _raise_if_client_disconnected(
    disconnect_checker: Optional[DisconnectChecker],
) -> None:
    if disconnect_checker and await disconnect_checker():
        raise asyncio.CancelledError


async def _generate_structured_content(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker],
    text_chunk_callback: Optional[TextChunkCallback] = None,
    call_timeout_seconds: Optional[float] = None,
    **kwargs: Any,
) -> _StructuredGenerationResult:
    generate_call = provider_generate_call(
        client,
        provider_id=_provider_receipt_identity(),
        kwargs=kwargs,
        idempotency_key=current_provider_idempotency_key(),
        timeout_seconds=call_timeout_seconds,
    )
    if disconnect_checker is None and text_chunk_callback is None:
        response = await _await_provider_call(generate_call)
        return _StructuredGenerationResult(
            content=extract_structured_content(response.content),
            response=response,
        )

    completion_content: Any = None
    completion: object | None = None
    streamed_text: list[str] = []
    async for event in _stream_generate_events_unadmitted(
        client,
        disconnect_checker=disconnect_checker,
        **{**kwargs, "stream": True},
    ):
        if isinstance(event, ResponseStreamCompletionChunk):
            completion = event
            completion_content = event.content
        elif getattr(event, "type", None) == "content":
            chunk = getattr(event, "chunk", None)
            if isinstance(chunk, str):
                streamed_text.append(chunk)
                if text_chunk_callback is not None:
                    await text_chunk_callback(chunk)

    content = extract_structured_content(completion_content)
    if content is not None:
        if text_chunk_callback is not None and not streamed_text:
            serialized = serialize_structured_content(completion_content)
            if serialized:
                await text_chunk_callback(serialized)
        return _StructuredGenerationResult(content=content, response=completion)
    return _StructuredGenerationResult(
        content=extract_structured_content("".join(streamed_text)),
        response=completion,
    )


def _exception_status_code(error: Exception) -> Optional[int]:
    """Return an HTTP-like status code from provider and normalized LLM errors.

    One reader for every provider path: the same structured status decides a
    transient retry here and whether an authored overload may be replayed
    (``utils/provider_overload.py``), so neither can drift into reading a
    number out of a message the other does not.
    """

    return provider_status_code(error)


async def _generate_structured_content_with_transient_retries(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker],
    is_retry: bool = False,
    semantic_slot: str,
    call_budget: _StructuredProviderCallBudget,
    call_timeout_seconds: Optional[float] = None,
    **kwargs: Any,
) -> Optional[dict]:
    """Retry only transient provider failures, then expose a retryable 503."""
    max_attempts = min(TRANSIENT_LLM_MAX_ATTEMPTS, call_budget.remaining)
    for attempt in range(1, max_attempts + 1):
        await _raise_if_client_disconnected(disconnect_checker)
        call_budget.consume()
        timer = ProviderCallTimer()
        provider = _provider_receipt_identity()
        model = str(kwargs.get("model") or "unknown")
        physical_semantic_slot = f"{semantic_slot}.attempt-{attempt}"
        entered_provider_boundary = False
        outcome = "succeeded"
        result: _StructuredGenerationResult | None = None
        try:
            try:
                async with provider_call_admission(
                    is_retry=is_retry or attempt > 1,
                    semantic_slot=physical_semantic_slot,
                    provider=provider,
                    model=model,
                    provider_idempotency_capable=supports_provider_idempotency(
                        client, provider_id=provider
                    ),
                ):
                    entered_provider_boundary = True
                    result = await _generate_structured_content(
                        client,
                        disconnect_checker=disconnect_checker,
                        call_timeout_seconds=call_timeout_seconds,
                        **kwargs,
                    )
            except BaseException as call_error:
                outcome = (
                    "cancelled"
                    if isinstance(call_error, asyncio.CancelledError)
                    else "failed"
                )
                raise
            finally:
                if entered_provider_boundary:
                    await record_provider_call(
                        provider=provider,
                        model=model,
                        response=(result.response if result is not None else None),
                        latency_ms=timer.elapsed_ms,
                        retry_class=(
                            "retry" if is_retry or attempt > 1 else "initial"
                        ),
                        outcome=outcome,
                    )
            if result is None:
                raise RuntimeError("structured_provider_result_missing")
            return result.content
        except Exception as error:
            status_code = _exception_status_code(error)
            is_transient = status_code in TRANSIENT_LLM_STATUS_CODES
            if not is_transient:
                raise

            if attempt == max_attempts or call_budget.remaining <= 0:
                LOGGER.error(
                    "Structured LLM generation unavailable after %s attempts "
                    "(last_status=%s)",
                    attempt,
                    status_code,
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The presentation model is temporarily unavailable after "
                        "multiple attempts. Please try again later."
                    ),
                    headers={"Retry-After": "10"},
                ) from error

            delay_seconds = min(
                TRANSIENT_LLM_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                TRANSIENT_LLM_RETRY_MAX_DELAY_SECONDS,
            )
            LOGGER.warning(
                "Structured LLM generation temporarily unavailable "
                "(status=%s, attempt=%s/%s); retrying in %.1fs",
                status_code,
                attempt,
                max_attempts,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)

    raise RuntimeError("Transient LLM retry loop exited unexpectedly")


def get_generate_kwargs(
    model: str,
    messages: Sequence[Message],
    max_tokens: Optional[int] = None,
    tools: Optional[list[LLMTool]] = None,
    response_format: Optional[ResponseFormat] = None,
    reasoning: Optional[ReasoningConfig] = None,
    stream: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "stream": stream,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if tools:
        kwargs["tools"] = tools
    if response_format is not None:
        kwargs["response_format"] = response_format
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    extra_body = get_extra_body()
    if extra_body:
        kwargs["extra_body"] = extra_body

    return kwargs

def structured_validation_feedback_user_message(
    content: dict,
    validation_errors: list[str],
) -> UserMessage:
    max_error_count = 10
    max_json_chars = 6000

    formatted_errors = validation_errors[:max_error_count]
    if len(validation_errors) > max_error_count:
        formatted_errors.append(
            f"...and {len(validation_errors) - max_error_count} more validation errors."
        )

    previous_response = json.dumps(
        content,
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if len(previous_response) > max_json_chars:
        previous_response = previous_response[:max_json_chars] + "\n... (truncated)"

    return UserMessage(
        content=(
            "The previous JSON response did not match the required response schema.\n\n"
            "Validation errors:\n"
            + "\n".join(f"- {error}" for error in formatted_errors)
            + "\n\nPrevious invalid JSON:\n"
            + f"```json\n{previous_response}\n```\n\n"
            + "Return corrected JSON only. Make sure it fully matches the required schema."
        )
    )


def _resolve_ref(schema: Any, root: dict) -> Any:
    """Resolve a local $ref ('#/$defs/X') against the root schema; passthrough otherwise."""
    if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if ref.startswith("#/"):
            node: Any = root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(node, dict):
                    node = node.get(part, {})
                else:
                    return {}
            return node if isinstance(node, dict) else {}
    return schema


def _schema_variant_label(schema: dict) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ""
    for discriminator in ("archetype", "type"):
        spec = properties.get(discriminator)
        if not isinstance(spec, dict):
            continue
        if isinstance(spec.get("const"), str):
            return spec["const"]
        values = spec.get("enum")
        if (
            isinstance(values, list)
            and len(values) == 1
            and isinstance(values[0], str)
        ):
            return values[0]
    return ""


def _schema_matches_value(schema: Any, value: Any, root: dict) -> bool:
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    return (
        (schema_type == "object" and isinstance(value, dict))
        or (schema_type == "array" and isinstance(value, list))
        or (schema_type == "string" and isinstance(value, str))
        or (
            schema_type == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        or (
            schema_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
        )
        or (schema_type == "boolean" and isinstance(value, bool))
        or (schema_type == "null" and value is None)
    )


def _compact_text(value: str, maximum: int) -> str:
    """Shorten at sentence or word boundaries while preserving leading meaning."""
    if len(value) <= maximum:
        return value
    if maximum <= 0:
        return ""

    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= maximum:
        return normalized

    segments = [
        match.group(0).strip()
        for match in re.finditer(r".+?(?:[.!?。！？](?=\s|$)|$)", normalized)
        if match.group(0).strip()
    ]
    complete = ""
    for segment in segments:
        candidate = f"{complete} {segment}".strip()
        if len(candidate) > maximum:
            break
        complete = candidate
    if complete:
        return complete

    ellipsis = "…" if maximum > 1 else ""
    boundary = maximum - len(ellipsis)
    prefix = normalized[:boundary].rstrip()
    for separator in ("; ", ": ", ", ", " "):
        split_at = prefix.rfind(separator)
        if split_at >= max(1, boundary // 2):
            prefix = prefix[:split_at].rstrip()
            break
    return (prefix + ellipsis)[:maximum]


def compact_to_schema(value: Any, schema: Any, root: dict) -> Any:
    """Compact only upper-bound overflow before requesting a full LLM rewrite."""
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return value

    for sub_schema in schema.get("allOf", []):
        value = compact_to_schema(value, sub_schema, root)

    for combiner in ("oneOf", "anyOf"):
        variants = schema.get(combiner)
        if not isinstance(variants, list):
            continue
        resolved_variants = [_resolve_ref(variant, root) for variant in variants]
        chosen = None
        if isinstance(value, dict):
            for variant in resolved_variants:
                if not isinstance(variant, dict):
                    continue
                label = _schema_variant_label(variant)
                if label and value.get("archetype", value.get("type")) == label:
                    chosen = variant
                    break
        chosen = chosen or next(
            (
                variant
                for variant in resolved_variants
                if _schema_matches_value(variant, value, root)
            ),
            None,
        )
        chosen = chosen or (resolved_variants[0] if resolved_variants else None)
        return compact_to_schema(value, chosen, root) if chosen else value

    schema_type = schema.get("type")
    if (
        schema_type == "object" or isinstance(schema.get("properties"), dict)
    ) and isinstance(value, dict):
        for key, child_schema in schema.get("properties", {}).items():
            if key in value:
                value[key] = compact_to_schema(value[key], child_schema, root)
        return value
    if schema_type == "array" and isinstance(value, list):
        items = schema.get("items", {})
        compacted = [compact_to_schema(item, items, root) for item in value]
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(compacted) > max_items:
            compacted = compacted[:max_items]
        return compacted
    if schema_type == "string" and isinstance(value, str):
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            return _compact_text(value, max_length)
    return value


def clamp_to_schema(value: Any, schema: Any, root: dict) -> Any:
    """Best-effort make `value` satisfy the schema's upper bounds: truncate strings
    past maxLength and arrays past maxItems (recursively, resolving $ref and the
    discriminated union by archetype/type). Last-resort salvage so a slightly
    over-long LLM response degrades to a valid (trimmed) deck instead of crashing.
    Cannot fix under-fill (min bounds); those are left for the retry loop."""
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return value

    for combiner in ("oneOf", "anyOf"):
        if combiner in schema:
            variants = [_resolve_ref(v, root) for v in schema[combiner]]
            chosen = None
            if isinstance(value, dict):
                for var in variants:
                    props = var.get("properties", {}) if isinstance(var, dict) else {}
                    for disc in ("archetype", "type"):
                        spec = props.get(disc, {})
                        allowed = (
                            [spec["const"]] if "const" in spec else spec.get("enum", [])
                        )
                        if allowed and value.get(disc) in allowed:
                            chosen = var
                            break
                    if chosen:
                        break
            chosen = chosen or (variants[0] if variants else None)
            return clamp_to_schema(value, chosen, root) if chosen else value

    stype = schema.get("type")
    if stype == "object" and isinstance(value, dict):
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                value[key] = clamp_to_schema(value[key], sub, root)
        return value
    if stype == "array" and isinstance(value, list):
        items = schema.get("items", {})
        out = [clamp_to_schema(v, items, root) for v in value]
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(out) > max_items:
            out = out[:max_items]
        return out
    if stype == "string" and isinstance(value, str):
        max_len = schema.get("maxLength")
        if isinstance(max_len, int) and len(value) > max_len:
            return value[:max_len].rstrip()
    return value


async def generate_structured_with_schema_retries(
    client: Any,
    model: str,
    *,
    messages: Sequence[Message],
    response_format: ResponseFormat,
    json_schema: dict,
    strict: bool = False,
    validate_schema: bool = False,
    validate_schema_max_loop_count: int = 4,
    disconnect_checker: Optional[DisconnectChecker] = None,
    text_chunk_callback: Optional[TextChunkCallback] = None,
    is_retry: bool = False,
    semantic_slot_suffix: str = "",
    semantic_slot_prefix: str = "structured",
    call_budget: Optional[StructuredProviderCallBudget] = None,
    call_timeout_seconds: Optional[float] = None,
) -> dict:
    """
    Parse retries (inner loop) plus optional JSON Schema validation feedback loops (outer loop),
    matching the overflow-mitigation behavior from structured generation with validate_schema.

    ``semantic_slot_prefix`` names this call site inside its provider stage. The
    durable admission key is (operation, stage, slide, semantic slot), so two
    different structured calls made inside ONE stage must not both take the
    default prefix: the second would be admitted as a replay of the first and
    refused with ``public_provider_terminal_replay_unsupported``. Callers that
    add a structured call to a stage that already has one pass their own prefix.

    ``call_budget`` is the physical-call budget for this logical call; a caller
    that runs the same logical call twice (the content brief and its retry)
    passes one budget to both runs so together they stay within
    ``PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS``.  ``call_timeout_seconds`` bounds
    each physical request at the transport (``provider_generate_call``) so a
    caller's ``asyncio.wait_for`` deadline also ends the HTTP call itself.
    """
    max_validation_loops = min(
        max(1, validate_schema_max_loop_count),
        PUBLIC_STRUCTURED_MAX_VALIDATION_LOOPS,
    )
    working_messages: list[Message] = list(messages)
    if call_budget is None:
        call_budget = StructuredProviderCallBudget()

    for validation_attempt in range(max_validation_loops):
        content: Optional[dict] = None
        for parse_attempt in range(PUBLIC_STRUCTURED_MAX_PARSE_ATTEMPTS):
            if call_budget.remaining <= 0:
                break
            await _raise_if_client_disconnected(disconnect_checker)
            semantic_slot = structured_semantic_slot(
                validation_attempt=validation_attempt,
                parse_attempt=parse_attempt,
                suffix=semantic_slot_suffix,
                prefix=semantic_slot_prefix,
            )
            content = await _generate_structured_content_with_transient_retries(
                client,
                disconnect_checker=disconnect_checker,
                is_retry=(
                    is_retry or validation_attempt > 0 or parse_attempt > 0
                ),
                semantic_slot=semantic_slot,
                call_budget=call_budget,
                call_timeout_seconds=call_timeout_seconds,
                text_chunk_callback=(
                    text_chunk_callback
                    if validation_attempt == 0 and parse_attempt == 0
                    else None
                ),
                **get_generate_kwargs(
                    model=model,
                    messages=working_messages,
                    response_format=response_format,
                ),
            )
            if content is not None:
                break
            if call_budget.remaining > 0:
                await asyncio.sleep(0.5 * (parse_attempt + 1))

        if content is None:
            raise HTTPException(
                status_code=400,
                detail="LLM did not return any content",
            )

        if not validate_schema:
            return content

        validation_errors = get_schema_validation_errors(
            json_schema,
            content,
            strict=strict,
        )

        if not validation_errors:
            return content

        formatted_validation_errors = " | ".join(validation_errors)
        compacted = compact_to_schema(content, json_schema, json_schema)
        remaining_after_compaction = get_schema_validation_errors(
            json_schema,
            compacted,
            strict=strict,
        )
        if not remaining_after_compaction:
            LOGGER.warning(
                "Validation errors fixed by field-level semantic compaction: %s",
                formatted_validation_errors,
            )
            return compacted

        if (
            validation_attempt == max_validation_loops - 1
            or call_budget.remaining <= 0
        ):
            # Last resort: clamp over-length strings / over-count arrays to the schema
            # so the response is valid (trimmed) rather than crashing the caller.
            clamped = clamp_to_schema(compacted, json_schema, json_schema)
            remaining = get_schema_validation_errors(json_schema, clamped, strict=strict)
            if remaining:
                LOGGER.warning(
                    "Validation errors remain after final schema clamp: %s",
                    " | ".join(remaining),
                )
            else:
                LOGGER.warning(
                    "Validation errors fixed by final schema clamp: %s",
                    formatted_validation_errors,
                )
            return clamped

        LOGGER.warning(
            "Validation error, attempting fix %s/%s: %s",
            validation_attempt + 1,
            max_validation_loops - 1,
            formatted_validation_errors,
        )
        working_messages.append(
            structured_validation_feedback_user_message(content, validation_errors)
        )

    raise HTTPException(status_code=400, detail="LLM did not return any content")


async def _stream_generate_events_unadmitted(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker] = None,
    inactivity_timeout_seconds: Optional[float] = None,
    **kwargs,
) -> AsyncGenerator[Any, None]:
    generate_call = provider_generate_call(
        client,
        provider_id=_provider_receipt_identity(),
        kwargs=kwargs,
        idempotency_key=current_provider_idempotency_key(),
    )
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()
    stop_requested = threading.Event()

    def enqueue(item: Any) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass

    def worker():
        events = None
        try:
            events = iter(generate_call())
            while not stop_requested.is_set():
                try:
                    event = next(events)
                except StopIteration:
                    break
                if stop_requested.is_set():
                    break
                enqueue(event)
        except Exception as exc:
            if not stop_requested.is_set():
                enqueue(exc)
        finally:
            if stop_requested.is_set() and events is not None:
                close = getattr(events, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        LOGGER.debug(
                            "Failed to close cancelled LLM stream",
                            exc_info=True,
                        )
            enqueue(sentinel)

    worker_task = asyncio.create_task(asyncio.to_thread(worker))
    completed = False
    last_event_at = loop.time()
    try:
        while True:
            await _raise_if_client_disconnected(disconnect_checker)
            poll_timeout = (
                CLIENT_DISCONNECT_POLL_SECONDS
                if disconnect_checker
                else inactivity_timeout_seconds
            )
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=poll_timeout,
                )
            except asyncio.TimeoutError:
                if (
                    inactivity_timeout_seconds is not None
                    and loop.time() - last_event_at >= inactivity_timeout_seconds
                ):
                    raise TimeoutError(
                        "The language model stopped responding. Please try again."
                    )
                continue
            last_event_at = loop.time()
            if item is sentinel:
                completed = True
                break
            if isinstance(item, Exception):
                raise item
            yield item
    except asyncio.CancelledError:
        LOGGER.info("LLM stream cancelled because the client disconnected")
        raise
    finally:
        stop_requested.set()
        if completed or worker_task.done():
            await worker_task
        else:
            worker_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )


async def stream_generate_events(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker] = None,
    inactivity_timeout_seconds: Optional[float] = None,
    is_retry: bool = False,
    semantic_slot_suffix: str = "",
    **kwargs: Any,
) -> AsyncGenerator[Any, None]:
    """Admit exactly one provider stream before its first network byte.

    The stream's durable identity is its semantic slot inside the active usage
    scope, so a caller that deliberately streams the same stage twice (the
    outline's page-count retry) passes ``is_retry`` and a ``semantic_slot_suffix``,
    exactly as ``generate_structured_with_schema_retries`` callers do. Without
    the suffix the second stream reopens the first one's terminal row and, for a
    provider that cannot replay, is refused with
    ``public_provider_terminal_replay_unsupported``.
    """

    timer = ProviderCallTimer()
    completion: object | None = None
    provider = _provider_receipt_identity()
    model = str(kwargs.get("model") or "unknown")
    entered_provider_boundary = False
    outcome = "succeeded"
    try:
        async with provider_call_admission(
            is_retry=is_retry,
            semantic_slot=f"stream.primary{semantic_slot_suffix}",
            provider=provider,
            model=model,
            provider_idempotency_capable=supports_provider_idempotency(
                client, provider_id=provider
            ),
        ):
            entered_provider_boundary = True
            async for event in _stream_generate_events_unadmitted(
                client,
                disconnect_checker=disconnect_checker,
                inactivity_timeout_seconds=inactivity_timeout_seconds,
                **kwargs,
            ):
                if isinstance(event, ResponseStreamCompletionChunk):
                    completion = event
                    continue
                yield event
    except BaseException as error:
        outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        raise
    finally:
        if entered_provider_boundary:
            await record_provider_call(
                provider=provider,
                model=model,
                response=completion,
                latency_ms=timer.elapsed_ms,
                outcome=outcome,
            )
    if completion is not None:
        yield completion
