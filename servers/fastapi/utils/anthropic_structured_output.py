"""Native Anthropic structured output for llmai ``response_format`` calls.

llmai's Anthropic client implements ``response_format`` by adding a
"response" tool and forcing it with ``tool_choice`` ``{"type": "tool"}`` (or
``{"type": "any"}`` next to caller tools). Claude Opus 5.5, Claude Sonnet 5.5
and Claude Fable 5.1 reject forced ``tool_choice`` with HTTP 400, so every
structured call failed on them. The Messages API's documented replacement is
structured outputs: ``output_config.format = {"type": "json_schema", ...}``,
with the JSON answer returned in the text content block.

:class:`RepositoryAnthropicClient` keeps llmai's request building, retries,
usage accounting and stream event handling, and changes only the structured
part: the response schema goes to ``output_config.format`` instead of a forced
tool, and the JSON text is parsed back into the ``dict`` callers already read
from ``response.content``. :func:`install_anthropic_structured_output_client`
makes ``llmai.get_client`` build this subclass for the Anthropic provider, so
every call site gets it without changing its imports. Other providers are not
touched.

The API compiles the schema into a grammar and accepts only part of JSON
Schema (``additionalProperties`` must be ``false``; ``minLength``,
``maxLength``, ``minimum``, ``maximum``, ``maxItems``, ``minItems`` above 1 and
similar constraints are rejected). :func:`native_output_schema` follows the
Anthropic SDK's ``transform_schema`` convention for those: the constraint is
removed from the wire schema and restated in the field's ``description``, so
the model still sees it and the caller's own validation of the original
schema keeps enforcing it. ``pattern`` is handled the same way: llmai never
sent it as an enforced constraint either (non-strict tools treat the schema as
a hint and strict tools drop it), and only simple regexes compile.

Two cases cannot use the grammar, and neither is narrowed silently:

* A schema the grammar cannot express (a free-form or map-typed object, tuple
  ``items``, more than 24 optional properties or 16 union-typed schemas) keeps
  llmai's forced response tool on models that accept it. On the models that
  reject forced ``tool_choice`` it takes the prompt-instructed path below.
* A schema the API refuses to compile (HTTP 400 "The compiled grammar is too
  large ..." -- the svg design-review schema does this on claude-sonnet-5-5)
  is retried once on the prompt-instructed path.

The prompt-instructed path sends no ``output_config.format`` and no tool: the
full original schema goes into the system prompt as an instruction to return
only a matching JSON object, and the reply is parsed leniently (code fences,
surrounding text). Nothing constrains that output, so the callers' own schema
validation is what enforces it; an unparseable reply stays text and takes the
callers' existing parse-retry handling.

The client also fills two model gaps llmai's bundled metadata leaves for the
5.5 / Fable 5.1 ids: a 32768 default output budget instead of llmai's 8192
fallback for unknown models, and the documented lowest thinking setting in
place of ``thinking: {"type": "disabled"}``, which those models reject.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Generator, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import llmai.client as llmai_client_module
from llmai.anthropic.client import AnthropicClient
from llmai.shared.messages import SystemMessage, TextContentPart
from llmai.shared.response_formats import (
    JSONObjectResponse,
    JSONSchemaResponse,
    ResponseFormat,
    get_response_schema,
)
from llmai.shared.responses import (
    ResponseContent,
    ResponseStreamChunk,
    ResponseStreamCompletionChunk,
    ResponseStreamContentChunk,
)

LOGGER = logging.getLogger(__name__)

#: String formats the structured-outputs grammar accepts.
SUPPORTED_STRING_FORMATS = frozenset(
    {
        "date-time",
        "time",
        "date",
        "duration",
        "email",
        "hostname",
        "uri",
        "ipv4",
        "ipv6",
        "uuid",
    }
)

#: Documented per-request structured-output limits (optional properties
#: across strict tools and output schemas; anyOf / type-array schemas).
MAX_OPTIONAL_PARAMETERS = 24
MAX_UNION_PARAMETERS = 16

#: Keywords sent unchanged (their subschemas, if any, are transformed).
_PASSTHROUGH_KEYWORDS = frozenset(
    {"type", "required", "enum", "const", "title", "default", "$ref"}
)

#: Annotations with no effect on the output; dropped from the wire schema.
_DROPPED_ANNOTATIONS = frozenset({"$schema", "$id", "$comment"})


class NativeSchemaUnsupported(ValueError):
    """The schema cannot be expressed as an Anthropic structured-output schema."""


def native_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the wire schema for ``output_config.format``.

    Raises :class:`NativeSchemaUnsupported` when the schema needs something the
    grammar cannot express without changing what the caller may receive.
    """

    if not isinstance(schema, Mapping):
        raise NativeSchemaUnsupported("$: the schema root is not an object")
    transformed = _transform_schema(schema, "$")
    optional, unions = _count_optional_and_unions(transformed)
    if optional > MAX_OPTIONAL_PARAMETERS:
        raise NativeSchemaUnsupported(
            f"$: {optional} optional properties exceed the structured-output "
            f"limit of {MAX_OPTIONAL_PARAMETERS}"
        )
    if unions > MAX_UNION_PARAMETERS:
        raise NativeSchemaUnsupported(
            f"$: {unions} union-typed schemas exceed the structured-output "
            f"limit of {MAX_UNION_PARAMETERS}"
        )
    return transformed


def _count_optional_and_unions(node: object) -> tuple[int, int]:
    """Count properties outside ``required`` and anyOf / type-array schemas.

    Each property is counted where it is defined (a ``$defs`` entry once, not
    per reference), so this is a lower bound of what the API counts: a schema
    over it is certain to be rejected, one under it may still be.
    """

    if not isinstance(node, Mapping):
        return 0, 0
    optional = unions = 0
    children: list[object] = []
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        required = set(node.get("required") or [])
        optional += sum(1 for name in properties if name not in required)
        children.extend(properties.values())
    for key in ("$defs", "definitions"):
        if isinstance(node.get(key), Mapping):
            children.extend(node[key].values())
    for key in ("anyOf", "allOf"):
        if isinstance(node.get(key), list):
            children.extend(node[key])
    if "anyOf" in node or isinstance(node.get("type"), list):
        unions += 1
    if isinstance(node.get("items"), Mapping):
        children.append(node["items"])
    for child in children:
        child_optional, child_unions = _count_optional_and_unions(child)
        optional += child_optional
        unions += child_unions
    return optional, unions


def _schema_types(node: Mapping[str, Any]) -> set[str]:
    declared = node.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {item for item in declared if isinstance(item, str)}
    return set()


def _transform_schema(node: object, path: str) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        raise NativeSchemaUnsupported(f"{path}: a subschema is not an object")

    transformed: dict[str, Any] = {}
    hints: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROPPED_ANNOTATIONS or key == "description":
            continue
        if key in _PASSTHROUGH_KEYWORDS:
            transformed[key] = deepcopy(value)
        elif key == "properties":
            if not isinstance(value, Mapping):
                raise NativeSchemaUnsupported(f"{path}.properties is not an object")
            transformed[key] = {
                name: _transform_schema(sub, f"{path}.properties.{name}")
                for name, sub in value.items()
            }
        elif key in ("$defs", "definitions"):
            if not isinstance(value, Mapping):
                raise NativeSchemaUnsupported(f"{path}.{key} is not an object")
            transformed[key] = {
                name: _transform_schema(sub, f"{path}.{key}.{name}")
                for name, sub in value.items()
            }
        elif key in ("anyOf", "allOf", "oneOf"):
            if not isinstance(value, list):
                raise NativeSchemaUnsupported(f"{path}.{key} is not a list")
            # oneOf is not in the grammar; like the Anthropic SDK, send it as
            # anyOf (the variants of a well-formed oneOf rarely overlap).
            target = "anyOf" if key == "oneOf" else key
            if target in transformed:
                raise NativeSchemaUnsupported(f"{path}: both oneOf and anyOf are set")
            transformed[target] = [
                _transform_schema(sub, f"{path}.{key}[{index}]")
                for index, sub in enumerate(value)
            ]
        elif key == "items":
            if not isinstance(value, Mapping):
                raise NativeSchemaUnsupported(f"{path}.items: tuple items are not supported")
            transformed[key] = _transform_schema(value, f"{path}.items")
        elif key == "format":
            if value in SUPPORTED_STRING_FORMATS:
                transformed[key] = value
            else:
                hints[key] = value
        elif key == "minItems":
            if value in (0, 1) and not isinstance(value, bool):
                transformed[key] = value
            else:
                hints[key] = value
        elif key == "additionalProperties":
            if value is not False:
                raise NativeSchemaUnsupported(
                    f"{path}: additionalProperties must be false (free-form maps "
                    "cannot be expressed)"
                )
        elif key == "patternProperties":
            raise NativeSchemaUnsupported(f"{path}: patternProperties is not supported")
        else:
            # minLength, maxLength, pattern, minimum, maximum, maxItems,
            # uniqueItems, ...: not enforceable by the grammar.
            hints[key] = deepcopy(value)

    is_object = "object" in _schema_types(node) or (
        "type" not in node and "properties" in node
    )
    if is_object:
        if "properties" not in node:
            raise NativeSchemaUnsupported(
                f"{path}: an object without properties (free-form object) cannot "
                "be expressed"
            )
        transformed["additionalProperties"] = False

    description = node.get("description")
    if hints:
        restated = "{" + ", ".join(
            f"{key}: {json.dumps(value, ensure_ascii=False)}"
            for key, value in hints.items()
        ) + "}"
        description = f"{description}\n\n{restated}" if description else restated
    if description is not None:
        transformed["description"] = description
    return transformed


def merge_output_config(
    extra_body: Mapping[str, Any] | None,
    effort_config: object,
    output_format: Mapping[str, Any],
) -> dict[str, Any]:
    """Return ``extra_body`` whose ``output_config`` also carries the format.

    The SDK merges ``extra_body`` over the request shallowly, so an
    ``extra_body.output_config`` replaces llmai's own ``output_config`` kwarg
    (the effort llmai derives from a reasoning config). Both effort sources are
    therefore folded in here, with the same precedence as before: the
    ``extra_body`` effort (authoring sets it) wins over llmai's.
    """

    merged_body = dict(extra_body or {})
    output_config: dict[str, Any] = {}
    if isinstance(effort_config, Mapping):
        output_config.update(effort_config)
    existing = merged_body.get("output_config")
    if existing is not None:
        if not isinstance(existing, Mapping):
            raise TypeError("extra_body.output_config must be a mapping")
        output_config.update(existing)
    if "format" in output_config and output_config["format"] != output_format:
        raise ValueError(
            "extra_body.output_config.format conflicts with the response_format schema"
        )
    output_config["format"] = dict(output_format)
    merged_body["output_config"] = output_config
    return merged_body


def _content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        if all(isinstance(part, TextContentPart) for part in content):
            return "".join(part.text for part in content)
    return None


def _json_object_candidates(text: str, *, lenient: bool) -> Iterable[str]:
    stripped = text.strip()
    yield stripped
    if not lenient:
        return
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = lines[1:]
        if body and body[-1].strip().startswith("```"):
            body = body[:-1]
        yield "\n".join(body).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        yield stripped[start : end + 1]


def parse_structured_text(content: object, *, lenient: bool = False) -> object:
    """Return the JSON object in a structured answer, else ``content`` unchanged.

    ``lenient`` (the prompt-instructed path, where nothing constrains the
    output) also accepts a Markdown code fence or text around the object.

    Output that is not a JSON object (a refusal or a ``max_tokens`` stop can
    end mid-document) stays text, exactly as llmai returns any other
    provider's unconstrained answer: llmai has no error type for unparseable
    structured output, and the callers' existing parse handling
    (``extract_structured_content`` and the bounded parse/validation retries in
    ``utils.llm_utils``) applies to it.
    """

    text = _content_text(content)
    if text is None or not text.strip():
        return content
    for candidate in _json_object_candidates(text, lenient=lenient):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return content


def _with_parsed_content(result: Any, *, lenient: bool = False) -> Any:
    parsed = parse_structured_text(result.content, lenient=lenient)
    if parsed is result.content:
        return result
    return result.model_copy(update={"content": parsed})


def _structured_stream_events(
    events: Generator[Any, None, None] | Iterable[Any], *, lenient: bool = False
):
    """Hide the JSON text deltas and parse the completion, as llmai's tool path did.

    With a forced response tool llmai emitted no content chunks for a
    structured answer and delivered the object only on the completion chunk;
    the native format streams the same object as text, which is kept off the
    content channel so callers see the same event sequence.
    """

    try:
        for event in events:
            if isinstance(event, ResponseStreamContentChunk):
                continue
            if isinstance(event, ResponseStreamChunk) and event.chunk_type == "content":
                continue
            if isinstance(event, ResponseStreamCompletionChunk):
                event = _with_parsed_content(event, lenient=lenient)
            yield event
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


#: Messages returned (HTTP 400) when the API cannot compile an output schema.
_GRAMMAR_REJECTION_MARKERS = (
    "compiled grammar is too large",
    "schema is too complex",
)


def is_grammar_rejection(error: BaseException) -> bool:
    """Recognize the API refusing to compile a structured-output schema.

    Walks the llmai error and its cause (llmai wraps the SDK's
    ``BadRequestError`` in an ``LLMError`` carrying the status and the API's
    message), matching like llmai's own ``_is_strict_tool_retryable_error``.
    """

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(seen) < 6:
        seen.add(id(current))
        status = getattr(current, "status_code", None) or getattr(current, "code", None)
        message = getattr(current, "message", None)
        if not isinstance(message, str) or not message:
            message = str(current)
        lowered = message.lower()
        if status == 400 and any(marker in lowered for marker in _GRAMMAR_REJECTION_MARKERS):
            return True
        nested = getattr(current, "cause", None) or current.__cause__
        current = nested if isinstance(nested, BaseException) else None
    return False


def json_instruction(schema: Mapping[str, Any] | None) -> str:
    """The system-prompt addition for the prompt-instructed JSON path."""

    if not schema:
        return (
            "Respond with only a single JSON object. Do not wrap it in Markdown "
            "code fences and do not write anything before or after it."
        )
    return (
        "Respond with only a single JSON object that conforms to the JSON Schema "
        "below, including every constraint it states (required properties, "
        "enums, lengths, ranges and item counts). Do not wrap it in Markdown code "
        "fences and do not write anything before or after it.\n\nJSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    )


def with_json_instruction(messages: list[Any], schema: Mapping[str, Any] | None) -> list[Any]:
    """Append :func:`json_instruction` to the system prompt (llmai reads the first)."""

    instruction = json_instruction(schema)
    updated = list(messages)
    for index, message in enumerate(updated):
        if isinstance(message, SystemMessage):
            updated[index] = message.model_copy(
                update={"content": f"{message.content}\n\n{instruction}"}
            )
            return updated
    return [SystemMessage(content=instruction), *updated]


#: Models that reject ``tool_choice`` ``any`` / ``tool`` with HTTP 400.
FORCED_TOOL_CHOICE_REJECTED_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1"}
)
#: Models on which ``thinking: {"type": "disabled"}`` is a 400 at every effort
#: (thinking is always on; lower effort is the only control).
THINKING_ALWAYS_ON_MODELS = frozenset(
    {"claude-opus-5-5", "claude-fable-5", "claude-fable-5-1", "claude-mythos-5", "claude-mythos-5-1"}
)
#: Models whose lowest thinking setting is ``{"type": "between_tools"}``
#: (``disabled`` is a 400; ``between_tools`` is accepted at effort high or
#: below, with no other field in ``thinking``). Only Claude Sonnet 5.5.
BETWEEN_TOOLS_MODELS = frozenset({"claude-sonnet-5-5"})
#: Output budget for calls that set none on a model llmai has no metadata
#: for. llmai falls back to 8192 there, while every current Claude model
#: allows 128K and the 5.5 / Fable 5.1 models think adaptively by default, so
#: thinking alone could use most of it. 32768 is what llmai's balanced profile
#: gives the Claude models it knows (min(advertised, 32768)).
UNKNOWN_MODEL_DEFAULT_MAX_TOKENS = 32_768


def _model_key(model: object) -> str:
    return str(model or "").strip().lower()


def rejects_forced_tool_choice(model: object) -> bool:
    return _model_key(model) in FORCED_TOOL_CHOICE_REJECTED_MODELS


_WARNED_PROMPT_JSON: set[tuple[str, str]] = set()
_WARNED_LOCK = threading.Lock()


def _warn_prompt_json_once(name: str, reason: str, detail: object) -> None:
    with _WARNED_LOCK:
        if (name, reason) in _WARNED_PROMPT_JSON:
            return
        _WARNED_PROMPT_JSON.add((name, reason))
    LOGGER.warning(
        "anthropic_structured_output_prompt_json name=%s reason=%s detail=%s; "
        "the schema is sent as a system-prompt instruction and the reply parsed "
        "as JSON (not grammar-constrained; the caller's validation enforces it)",
        name,
        reason,
        detail,
    )


@dataclass(frozen=True)
class _StructuredPlan:
    """How one structured request is sent.

    ``llmai``: unchanged llmai request (no structured format, or llmai's
    forced tool on a model that accepts it); ``native``: ``output_config``
    format; ``prompt``: schema as a system-prompt instruction.
    """

    kind: str
    name: str = "response"
    schema: Mapping[str, Any] | None = None
    output_format: Mapping[str, Any] | None = None


class RepositoryAnthropicClient(AnthropicClient):
    """llmai's Anthropic client with native structured output."""

    # -- output budget -----------------------------------------------------

    def generate(
        self,
        *,
        model: str,
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        profile: Any = None,
        **kwargs: Any,
    ):
        if (
            max_tokens is None
            and max_output_tokens is None
            and profile is None
            and self._generation_defaults.max_output_tokens is None
            and self._advertised_output_limit(model) is None
        ):
            max_tokens = UNKNOWN_MODEL_DEFAULT_MAX_TOKENS
        return super().generate(
            model=model,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
            profile=profile,
            **kwargs,
        )

    def _advertised_output_limit(self, model: str) -> int | None:
        advertised = self.get_model_capabilities(model).max_output_tokens.value
        return advertised if isinstance(advertised, int) and advertised > 0 else None

    # -- thinking ------------------------------------------------------------

    def _thinking_extra_body(
        self, model: str, extra_body: dict | None, reasoning_effort: Any
    ) -> dict | None:
        """Translate a disabled-thinking request for models that reject it.

        ``{"type": "disabled"}`` (authoring's ``none`` effort through
        ``extra_body``, or llmai's own from a disabled reasoning config) is a
        400 on the always-thinking models and on Claude Sonnet 5.5. There it
        becomes the documented lowest setting: ``between_tools`` on Claude
        Sonnet 5.5 (adaptive at effort xhigh/max, where ``between_tools`` is
        rejected), adaptive thinking at effort ``low`` (unless an effort is
        already set) on the always-thinking models. Other models are left as
        they are.
        """

        key = _model_key(model)
        if key not in THINKING_ALWAYS_ON_MODELS and key not in BETWEEN_TOOLS_MODELS:
            return extra_body
        body = dict(extra_body or {})
        thinking = (
            body["thinking"]
            if "thinking" in body
            else self._get_anthropic_thinking_or_omit(reasoning_effort)
        )
        if not (isinstance(thinking, Mapping) and thinking.get("type") == "disabled"):
            return extra_body
        llmai_output_config = self._get_anthropic_output_config_or_omit(reasoning_effort)
        output_config: dict[str, Any] = {}
        if isinstance(llmai_output_config, Mapping):
            output_config.update(llmai_output_config)
        if isinstance(body.get("output_config"), Mapping):
            output_config.update(body["output_config"])
        effort = output_config.get("effort")
        if key in BETWEEN_TOOLS_MODELS and effort not in ("xhigh", "max"):
            body["thinking"] = {"type": "between_tools"}
        else:
            body["thinking"] = {"type": "adaptive"}
            if effort is None:
                output_config["effort"] = "low"
        if output_config:
            body["output_config"] = output_config
        return body

    # -- structured output ---------------------------------------------------

    def _structured_plan(
        self, model: str, response_format: ResponseFormat | None
    ) -> _StructuredPlan:
        if isinstance(response_format, JSONObjectResponse):
            # A free-form object cannot be expressed as a grammar (objects
            # must be closed); keep llmai's forced tool where it is accepted.
            if rejects_forced_tool_choice(model):
                _warn_prompt_json_once("response", "json_object", "free-form object")
                return _StructuredPlan("prompt")
            return _StructuredPlan("llmai")
        if not isinstance(response_format, JSONSchemaResponse):
            return _StructuredPlan("llmai")
        schema = get_response_schema(response_format)
        if not schema:
            return _StructuredPlan("llmai")
        name = response_format.name or "response"
        try:
            wire_schema = native_output_schema(schema)
        except NativeSchemaUnsupported as error:
            if rejects_forced_tool_choice(model):
                _warn_prompt_json_once(name, "schema_not_expressible", error)
                return _StructuredPlan("prompt", name=name, schema=schema)
            LOGGER.warning(
                "anthropic_native_structured_output_unavailable name=%s reason=%s; "
                "using llmai's forced response tool",
                name,
                error,
            )
            return _StructuredPlan("llmai")
        return _StructuredPlan(
            "native",
            name=name,
            schema=schema,
            output_format={"type": "json_schema", "schema": wire_schema},
        )

    def _native_extra_body(
        self,
        extra_body: dict | None,
        reasoning_effort: Any,
        output_format: Mapping[str, Any],
    ) -> dict[str, Any]:
        return merge_output_config(
            extra_body,
            self._get_anthropic_output_config_or_omit(reasoning_effort),
            output_format,
        )

    def _generate_once(
        self,
        *,
        model: str,
        messages: list[Any],
        response_format: ResponseFormat | None = None,
        reasoning_effort: Any = None,
        extra_body: dict | None = None,
        **kwargs: Any,
    ) -> ResponseContent:
        extra_body = self._thinking_extra_body(model, extra_body, reasoning_effort)
        plan = self._structured_plan(model, response_format)
        if plan.kind == "llmai":
            return super()._generate_once(
                model=model,
                messages=messages,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
                **kwargs,
            )
        if plan.kind == "native":
            try:
                result = super()._generate_once(
                    model=model,
                    messages=messages,
                    response_format=None,
                    reasoning_effort=reasoning_effort,
                    extra_body=self._native_extra_body(
                        extra_body, reasoning_effort, plan.output_format
                    ),
                    **kwargs,
                )
            except Exception as error:
                if not is_grammar_rejection(error):
                    raise
                _warn_prompt_json_once(plan.name, "grammar_rejected", error)
            else:
                return _with_parsed_content(result)
        result = super()._generate_once(
            model=model,
            messages=with_json_instruction(messages, plan.schema),
            response_format=None,
            reasoning_effort=reasoning_effort,
            extra_body=extra_body,
            **kwargs,
        )
        return _with_parsed_content(result, lenient=True)

    def _generate_stream(
        self,
        *,
        model: str,
        messages: list[Any],
        response_format: ResponseFormat | None = None,
        reasoning_effort: Any = None,
        extra_body: dict | None = None,
        **kwargs: Any,
    ):
        extra_body = self._thinking_extra_body(model, extra_body, reasoning_effort)
        plan = self._structured_plan(model, response_format)
        if plan.kind == "llmai":
            return super()._generate_stream(
                model=model,
                messages=messages,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
                **kwargs,
            )
        return self._structured_stream(
            plan,
            model=model,
            messages=messages,
            reasoning_effort=reasoning_effort,
            extra_body=extra_body,
            **kwargs,
        )

    def _structured_stream(
        self,
        plan: _StructuredPlan,
        *,
        model: str,
        messages: list[Any],
        reasoning_effort: Any,
        extra_body: dict | None,
        **kwargs: Any,
    ):
        if plan.kind == "native":
            started = False
            try:
                for event in _structured_stream_events(
                    super()._generate_stream(
                        model=model,
                        messages=messages,
                        response_format=None,
                        reasoning_effort=reasoning_effort,
                        extra_body=self._native_extra_body(
                            extra_body, reasoning_effort, plan.output_format
                        ),
                        **kwargs,
                    )
                ):
                    started = True
                    yield event
                return
            except Exception as error:
                # The compile error is the HTTP status of the stream request,
                # raised before the first event; never retry after output.
                if started or not is_grammar_rejection(error):
                    raise
                _warn_prompt_json_once(plan.name, "grammar_rejected", error)
        yield from _structured_stream_events(
            super()._generate_stream(
                model=model,
                messages=with_json_instruction(messages, plan.schema),
                response_format=None,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
                **kwargs,
            ),
            lenient=True,
        )


def install_anthropic_structured_output_client() -> None:
    """Make ``llmai.get_client`` build :class:`RepositoryAnthropicClient`.

    ``llmai.get_client`` resolves the Anthropic client class from the
    ``llmai.client`` module globals at call time, so rebinding that one name
    covers every ``from llmai import get_client`` call site. Idempotent. Fails
    loudly if a future llmai moves the name, instead of silently reverting to
    the forced-tool path.
    """

    current = getattr(llmai_client_module, "AnthropicClient", None)
    if current is RepositoryAnthropicClient:
        return
    if current is not AnthropicClient:
        raise RuntimeError(
            "llmai.client.AnthropicClient is not llmai's AnthropicClient; the "
            "native structured-output client cannot be installed"
        )
    llmai_client_module.AnthropicClient = RepositoryAnthropicClient
