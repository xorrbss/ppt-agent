import asyncio
import json
import logging
import math
import re
import threading
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import dirtyjson
from fastapi import HTTPException
from llmai.shared import (
    LLMTool,
    Message,
    ReasoningConfig,
    ResponseFormat,
    ResponseStreamCompletionChunk,
    UserMessage,
    normalize_content_parts,
)

from utils.llm_config import get_extra_body
from utils.schema_utils import get_schema_validation_errors
from services.provider_usage_instrumentation import provider_call_admission
LOGGER = logging.getLogger(__name__)
CLIENT_DISCONNECT_POLL_SECONDS = 0.1
TRANSIENT_LLM_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_LLM_MAX_ATTEMPTS = 4
TRANSIENT_LLM_RETRY_BASE_DELAY_SECONDS = 1.0
TRANSIENT_LLM_RETRY_MAX_DELAY_SECONDS = 8.0
DisconnectChecker = Callable[[], Awaitable[bool]]
TextChunkCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class TextGenerationMetrics:
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tokens_per_second: float
    duration_seconds: float
    estimated: bool
    thinking_tokens: Optional[int] = None
    thinking_tokens_estimated: bool = False
    supports_thinking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "tokens_per_second": round(self.tokens_per_second, 2),
            "duration_seconds": round(self.duration_seconds, 3),
            "estimated": self.estimated,
            "thinking_tokens": self.thinking_tokens,
            "thinking_tokens_estimated": self.thinking_tokens_estimated,
            "supports_thinking": self.supports_thinking,
        }


@dataclass(frozen=True)
class TextLengthLimit:
    path: str
    recommended: int
    maximum: int


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
    **kwargs: Any,
) -> Optional[dict]:
    if disconnect_checker is None and text_chunk_callback is None:
        response = await asyncio.to_thread(client.generate, **kwargs)
        return extract_structured_content(response.content)

    completion_content: Any = None
    streamed_text: list[str] = []
    async for event in stream_generate_events(
        client,
        disconnect_checker=disconnect_checker,
        **{**kwargs, "stream": True},
    ):
        if isinstance(event, ResponseStreamCompletionChunk):
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
        return content
    return extract_structured_content("".join(streamed_text))


def _exception_status_code(error: Exception) -> Optional[int]:
    """Return an HTTP-like status code from provider and normalized LLM errors."""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    response = getattr(error, "response", None)
    response_status_code = getattr(response, "status_code", None)
    if isinstance(response_status_code, int):
        return response_status_code
    return None


async def _generate_structured_content_with_transient_retries(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker],
    is_retry: bool = False,
    **kwargs: Any,
) -> Optional[dict]:
    """Retry only transient provider failures, then expose a retryable 503."""
    for attempt in range(1, TRANSIENT_LLM_MAX_ATTEMPTS + 1):
        await _raise_if_client_disconnected(disconnect_checker)
        try:
            async with provider_call_admission(is_retry=is_retry or attempt > 1):
                return await _generate_structured_content(
                    client,
                    disconnect_checker=disconnect_checker,
                    **kwargs,
                )
        except Exception as error:
            status_code = _exception_status_code(error)
            is_transient = status_code in TRANSIENT_LLM_STATUS_CODES
            if not is_transient:
                raise

            if attempt == TRANSIENT_LLM_MAX_ATTEMPTS:
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
                TRANSIENT_LLM_MAX_ATTEMPTS,
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


def estimate_text_tokens(value: str) -> int:
    """Return a stable approximation when a provider omits token usage."""
    return max(1, round(len(value) / 4)) if value else 0


def estimate_thinking_tokens(value: str) -> int:
    """Match llmai's visible-reasoning estimate for streamed thinking chunks."""
    return math.ceil(len(value.encode("utf-8")) / 4) if value else 0


def estimate_message_tokens(messages: Sequence[Message]) -> int:
    return sum(
        estimate_text_tokens(extract_text(getattr(message, "content", None)) or "")
        for message in messages
    )


def _usage_token_value(usage: Any, *names: str) -> Optional[int]:
    for name in names:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _usage_thinking_tokens(usage: Any) -> tuple[Optional[int], bool]:
    """Return llmai's best thinking count and whether that count is estimated."""
    if usage is None:
        return None, False

    reasoning = (
        usage.get("reasoning")
        if isinstance(usage, dict)
        else getattr(usage, "reasoning", None)
    )
    billed_tokens = _usage_token_value(reasoning, "billed_tokens")
    if billed_tokens is not None:
        billed_estimated = (
            reasoning.get("billed_estimated", False)
            if isinstance(reasoning, dict)
            else getattr(reasoning, "billed_estimated", False)
        )
        return billed_tokens, bool(billed_estimated)

    visible_tokens = _usage_token_value(reasoning, "visible_tokens")
    if visible_tokens is not None:
        return visible_tokens, True

    return _usage_token_value(usage, "thinking_tokens", "reasoning_tokens"), False


def build_text_generation_metrics(
    *,
    model: str,
    messages: Sequence[Message],
    content: str,
    streamed_thinking: str,
    completion: Any,
    started_at: float,
    model_supports_thinking: bool = False,
) -> TextGenerationMetrics:
    usage = getattr(completion, "usage", None) if completion is not None else None
    exact_input_tokens = _usage_token_value(usage, "input_tokens", "prompt_tokens")
    exact_output_tokens = _usage_token_value(
        usage, "output_tokens", "completion_tokens"
    )
    input_tokens = (
        exact_input_tokens
        if exact_input_tokens is not None
        else estimate_message_tokens(messages)
    )
    output_tokens = (
        exact_output_tokens
        if exact_output_tokens is not None
        else estimate_text_tokens(content)
    )
    thinking_tokens, thinking_tokens_estimated = _usage_thinking_tokens(usage)
    if thinking_tokens is None and streamed_thinking:
        thinking_tokens = estimate_thinking_tokens(streamed_thinking)
        thinking_tokens_estimated = True

    supports_thinking = bool(
        model_supports_thinking
        or thinking_tokens is not None
        or streamed_thinking
    )
    if (
        supports_thinking
        and exact_output_tokens is not None
        and (thinking_tokens is None or thinking_tokens_estimated)
    ):
        inferred_thinking_tokens = max(
            0,
            exact_output_tokens - estimate_text_tokens(content),
        )
        if thinking_tokens is None or inferred_thinking_tokens > thinking_tokens:
            thinking_tokens = inferred_thinking_tokens
            thinking_tokens_estimated = True
    if supports_thinking and thinking_tokens is None:
        thinking_tokens = 0
        thinking_tokens_estimated = True

    duration_seconds = (
        getattr(completion, "duration_seconds", None)
        if completion is not None
        else None
    )
    if not isinstance(duration_seconds, (int, float)) or duration_seconds <= 0:
        duration_seconds = max(time.perf_counter() - started_at, 1e-9)
    total_tokens = _usage_token_value(usage, "total_tokens")
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    return TextGenerationMetrics(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        tokens_per_second=output_tokens / duration_seconds,
        duration_seconds=duration_seconds,
        estimated=exact_input_tokens is None or exact_output_tokens is None,
        thinking_tokens=thinking_tokens,
        thinking_tokens_estimated=thinking_tokens_estimated,
        supports_thinking=supports_thinking,
    )


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


def _schema_allows_string(
    schema: Any,
    root: dict,
    active_refs: set[str] | None = None,
) -> bool:
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "string" or (
        isinstance(schema_type, list) and "string" in schema_type
    ):
        return True
    if isinstance(schema.get("const"), str):
        return True
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and any(
        isinstance(value, str) for value in enum_values
    ):
        return True

    seen_refs = active_refs if active_refs is not None else set()
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref not in seen_refs:
        seen_refs.add(ref)
        if _schema_allows_string(_resolve_ref(schema, root), root, seen_refs):
            seen_refs.remove(ref)
            return True
        seen_refs.remove(ref)

    for combiner in ("allOf", "oneOf", "anyOf"):
        variants = schema.get(combiner)
        if isinstance(variants, list) and any(
            _schema_allows_string(variant, root, seen_refs)
            for variant in variants
        ):
            return True
    return False


def extract_text_length_limits(
    schema: dict,
    *,
    recommended_ratio: float = 0.8,
) -> list[TextLengthLimit]:
    """Return deterministic text budgets from a local JSON Schema.

    Only explicit ``maxLength`` constraints are surfaced. Unsupported or
    malformed schema shapes are ignored so prompt generation remains
    fail-closed instead of inventing limits.
    """
    if not isinstance(schema, dict):
        return []
    ratio = min(0.85, max(0.75, recommended_ratio))
    limits: dict[str, int] = {}
    active_refs: set[str] = set()

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        allows_string = _schema_allows_string(node, schema)
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref not in active_refs:
                active_refs.add(ref)
                visit(_resolve_ref(node, schema), path)
                active_refs.remove(ref)

        max_length = node.get("maxLength")
        if (
            allows_string
            and isinstance(max_length, int)
            and not isinstance(max_length, bool)
            and max_length >= 0
            and path
        ):
            limits[path] = min(limits.get(path, max_length), max_length)

        for combiner in ("allOf", "oneOf", "anyOf"):
            variants = node.get(combiner)
            if not isinstance(variants, list):
                continue
            for variant in variants:
                resolved = _resolve_ref(variant, schema)
                label = (
                    _schema_variant_label(resolved)
                    if isinstance(resolved, dict)
                    else ""
                )
                variant_path = f"{path}.<{label}>" if label and path else path
                visit(variant, variant_path)

        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                child_path = f"{path}.{name}" if path else name
                visit(child, child_path)

        items = node.get("items")
        if isinstance(items, dict):
            visit(items, f"{path}[]" if path else "[]")
        prefix_items = node.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child in enumerate(prefix_items):
                child_path = f"{path}[{index}]" if path else f"[{index}]"
                visit(child, child_path)

    visit(schema, "")
    return [
        TextLengthLimit(
            path=path,
            recommended=max(1, int(maximum * ratio)) if maximum else 0,
            maximum=maximum,
        )
        for path, maximum in sorted(limits.items())
    ]


def format_text_length_guidance(schema: dict) -> str:
    limits = extract_text_length_limits(schema)
    if not limits:
        return ""
    lines = [
        "# Text Length Budgets",
        (
            "Write to the recommended length when the source supports it. The "
            "absolute maximum is a hard limit. Remove repetition or rephrase; "
            "never end a title or sentence mid-word."
        ),
    ]
    lines.extend(
        (
            f"- `{limit.path}`: recommended <= {limit.recommended} characters; "
            f"absolute maximum {limit.maximum} characters."
        )
        for limit in limits
    )
    return "\n" + "\n".join(lines) + "\n"


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
) -> dict:
    """
    Parse retries (inner loop) plus optional JSON Schema validation feedback loops (outer loop),
    matching the overflow-mitigation behavior from structured generation with validate_schema.
    """
    max_validation_loops = max(1, validate_schema_max_loop_count)
    working_messages: list[Message] = list(messages)

    for validation_attempt in range(max_validation_loops):
        content: Optional[dict] = None
        for attempt in range(3):
            await _raise_if_client_disconnected(disconnect_checker)
            content = await _generate_structured_content_with_transient_retries(
                client,
                disconnect_checker=disconnect_checker,
                is_retry=validation_attempt > 0,
                text_chunk_callback=(
                    text_chunk_callback
                    if validation_attempt == 0 and attempt == 0
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
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))

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

        if validation_attempt == max_validation_loops - 1:
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


def extract_text(content: Any) -> Optional[str]:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        joined = "".join(parts)
        return joined or None
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return None


def extract_structured_content(content: Any) -> Optional[dict]:
    if content is None:
        return None
    if isinstance(content, dict):
        return content
    if hasattr(content, "model_dump"):
        dumped = content.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped

    raw_text = extract_text(content)
    if not raw_text:
        return None

    try:
        parsed = dirtyjson.loads(raw_text)
    except Exception:
        return None

    if isinstance(parsed, dict):
        return dict(parsed)
    return None


def serialize_structured_content(content: Any) -> Optional[str]:
    parsed = extract_structured_content(content)
    if parsed is not None:
        return json.dumps(parsed, ensure_ascii=False)

    raw_text = extract_text(content)
    if raw_text:
        return raw_text
    return None


def message_content_to_text(content: Sequence[Any] | str | None) -> Optional[str]:
    joined = "".join(
        part.text
        for part in normalize_content_parts(content)
        if isinstance(getattr(part, "text", None), str)
    )
    return joined or None


async def _stream_generate_events_unadmitted(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker] = None,
    inactivity_timeout_seconds: Optional[float] = None,
    **kwargs,
) -> AsyncGenerator[Any, None]:
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
            events = iter(client.generate(**kwargs))
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
    **kwargs: Any,
) -> AsyncGenerator[Any, None]:
    """Admit exactly one provider stream before its first network byte."""

    async with provider_call_admission():
        async for event in _stream_generate_events_unadmitted(
            client,
            disconnect_checker=disconnect_checker,
            inactivity_timeout_seconds=inactivity_timeout_seconds,
            **kwargs,
        ):
            yield event
