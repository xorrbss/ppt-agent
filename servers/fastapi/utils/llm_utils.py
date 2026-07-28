import asyncio
import json
import logging
import re
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

import dirtyjson
from fastapi import HTTPException
from llmai.shared import (
    LLMTool,
    Message,
    ResponseFormat,
    ResponseStreamCompletionChunk,
    UserMessage,
    normalize_content_parts,
)

from utils.llm_config import get_extra_body
from utils.schema_utils import get_schema_validation_errors


LOGGER = logging.getLogger(__name__)
CLIENT_DISCONNECT_POLL_SECONDS = 0.1
DisconnectChecker = Callable[[], Awaitable[bool]]


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
    **kwargs: Any,
) -> Optional[dict]:
    if disconnect_checker is None:
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

    content = extract_structured_content(completion_content)
    if content is not None:
        return content
    return extract_structured_content("".join(streamed_text))


def get_generate_kwargs(
    model: str,
    messages: Sequence[Message],
    max_tokens: Optional[int] = None,
    tools: Optional[list[LLMTool]] = None,
    response_format: Optional[ResponseFormat] = None,
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


def _schema_variant_label(schema: Any, root: dict) -> Optional[str]:
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return None
    for discriminator in ("archetype", "type"):
        spec = _resolve_ref(properties.get(discriminator), root)
        if not isinstance(spec, dict):
            continue
        if isinstance(spec.get("const"), str):
            return spec["const"]
        enum = spec.get("enum")
        if isinstance(enum, list) and len(enum) == 1 and isinstance(enum[0], str):
            return enum[0]
    return None


def extract_text_length_limits(
    schema: dict,
    *,
    recommended_ratio: float = 0.8,
) -> list[TextLengthLimit]:
    """Return text budgets from a JSON Schema, including referenced union branches.

    Paths use ``[]`` for homogeneous arrays and ``<name>`` for discriminated
    oneOf/anyOf branches, for example ``slides[].<cover>.title``.
    """
    ratio = min(0.85, max(0.75, recommended_ratio))
    limits: dict[str, int] = {}

    def walk(node: Any, path: str, ref_stack: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in ref_stack:
                return
            resolved = _resolve_ref(node, schema)
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            if siblings and isinstance(resolved, dict):
                resolved = {**resolved, **siblings}
            walk(resolved, path, ref_stack | {ref})
            return

        max_length = node.get("maxLength")
        if isinstance(max_length, int) and max_length >= 0 and path:
            previous = limits.get(path)
            limits[path] = max_length if previous is None else min(previous, max_length)

        for combiner in ("allOf", "oneOf", "anyOf"):
            variants = node.get(combiner)
            if not isinstance(variants, list):
                continue
            for variant in variants:
                label = (
                    _schema_variant_label(variant, schema)
                    if combiner in {"oneOf", "anyOf"}
                    else None
                )
                variant_path = f"{path}.<{label}>" if label and path else path
                walk(variant, variant_path, ref_stack)

        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                child_path = f"{path}.{name}" if path else name
                walk(child, child_path, ref_stack)

        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]" if path else "[]", ref_stack)
        elif isinstance(items, list):
            for index, child in enumerate(items):
                walk(child, f"{path}[{index}]" if path else f"[{index}]", ref_stack)

        prefix_items = node.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child in enumerate(prefix_items):
                walk(child, f"{path}[{index}]" if path else f"[{index}]", ref_stack)

    walk(schema, "", frozenset())
    return [
        TextLengthLimit(
            path=path,
            recommended=max(1, int(maximum * ratio)) if maximum else 0,
            maximum=maximum,
        )
        for path, maximum in sorted(limits.items())
    ]


def format_text_length_guidance(
    schema: dict,
    *,
    recommended_ratio: float = 0.8,
) -> str:
    limits = extract_text_length_limits(
        schema,
        recommended_ratio=recommended_ratio,
    )
    if not limits:
        return ""
    entries = "\n".join(
        f"- `{limit.path}`: recommended <= {limit.recommended} characters; "
        f"absolute maximum {limit.maximum} characters."
        for limit in limits
    )
    return (
        "\n# Text Length Budgets\n"
        "Write to the recommended length from the start; the absolute maximum is a "
        "hard schema limit, not a target. Keep titles and labels compact. Keep body "
        "copy and speaker notes complete and meaningful within their budgets. Remove "
        "repetition, filler, and decorative modifiers before dropping facts, numbers, "
        "names, or conclusions. Never end a title or sentence mid-word.\n"
        f"{entries}\n"
    )


def _schema_matches_value(schema: Any, value: Any, root: dict) -> bool:
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    return (
        (schema_type == "object" and isinstance(value, dict))
        or (schema_type == "array" and isinstance(value, list))
        or (schema_type == "string" and isinstance(value, str))
        or (schema_type == "number" and isinstance(value, (int, float)))
        or (schema_type == "integer" and isinstance(value, int))
        or (schema_type == "boolean" and isinstance(value, bool))
        or (schema_type == "null" and value is None)
    )


def _compact_text(value: str, maximum: int) -> str:
    """Shorten at sentence/word boundaries, preserving complete leading ideas."""
    if len(value) <= maximum:
        return value
    if maximum <= 0:
        return ""

    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= maximum:
        return normalized

    # Prefer one or more complete sentences/clauses over a raw character slice.
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
    """Compact only overflowing fields before considering a full LLM correction.

    Unlike ``clamp_to_schema``, strings prefer complete sentence/word boundaries.
    Arrays keep their existing order and discard only trailing overflow items.
    """
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict):
        return value

    for sub_schema in schema.get("allOf", []):
        value = compact_to_schema(value, sub_schema, root)

    for combiner in ("oneOf", "anyOf"):
        if combiner not in schema:
            continue
        variants = [_resolve_ref(variant, root) for variant in schema[combiner]]
        chosen = None
        if isinstance(value, dict):
            for variant in variants:
                label = _schema_variant_label(variant, root)
                if label and value.get("archetype", value.get("type")) == label:
                    chosen = variant
                    break
        chosen = chosen or next(
            (variant for variant in variants if _schema_matches_value(variant, value, root)),
            None,
        )
        chosen = chosen or (variants[0] if variants else None)
        return compact_to_schema(value, chosen, root) if chosen else value

    schema_type = schema.get("type")
    if (schema_type == "object" or "properties" in schema) and isinstance(value, dict):
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
            content = await _generate_structured_content(
                client,
                disconnect_checker=disconnect_checker,
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
        # Fix isolated upper-bound overflow fields before asking the LLM to
        # regenerate the whole structured response. This preserves all sibling
        # fields and prefers sentence/word boundaries for strings.
        compacted = compact_to_schema(content, json_schema, json_schema)
        remaining = get_schema_validation_errors(
            json_schema,
            compacted,
            strict=strict,
        )
        if not remaining:
            LOGGER.warning(
                "Validation errors fixed by field-level semantic compaction: %s",
                formatted_validation_errors,
            )
            return compacted

        if validation_attempt == max_validation_loops - 1:
            # Preserve clamp as the final availability safety net only.
            clamped = clamp_to_schema(compacted, json_schema, json_schema)
            remaining = get_schema_validation_errors(
                json_schema,
                clamped,
                strict=strict,
            )
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


async def stream_generate_events(
    client: Any,
    *,
    disconnect_checker: Optional[DisconnectChecker] = None,
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
    try:
        while True:
            await _raise_if_client_disconnected(disconnect_checker)
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=(
                        CLIENT_DISCONNECT_POLL_SECONDS
                        if disconnect_checker
                        else None
                    ),
                )
            except asyncio.TimeoutError:
                continue
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
