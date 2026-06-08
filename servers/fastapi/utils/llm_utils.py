import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Optional

import dirtyjson
from fastapi import HTTPException
from llmai.shared import (
    LLMTool,
    Message,
    ResponseFormat,
    UserMessage,
    normalize_content_parts,
)

from utils.llm_config import get_extra_body
from utils.schema_utils import get_schema_validation_errors


LOGGER = logging.getLogger(__name__)


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
                if isinstance(node, dict):
                    node = node.get(part, {})
                else:
                    return {}
            return node if isinstance(node, dict) else {}
    return schema


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
            response = await asyncio.to_thread(
                client.generate,
                **get_generate_kwargs(
                    model=model,
                    messages=working_messages,
                    response_format=response_format,
                ),
            )
            content = extract_structured_content(response.content)
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
        if validation_attempt == max_validation_loops - 1:
            # Last resort: clamp over-length strings / over-count arrays to the schema
            # so the response is valid (trimmed) rather than crashing the caller.
            clamped = clamp_to_schema(content, json_schema, json_schema)
            remaining = get_schema_validation_errors(json_schema, clamped, strict=strict)
            if remaining:
                LOGGER.warning(
                    "Validation errors after max fixes + clamp (returning clamped): %s",
                    " | ".join(remaining),
                )
            else:
                LOGGER.warning(
                    "Validation errors fixed by clamping after max fixes: %s",
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


async def stream_generate_events(client: Any, **kwargs) -> AsyncGenerator[Any, None]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def worker():
        try:
            for event in client.generate(**kwargs):
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    worker_task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await worker_task
