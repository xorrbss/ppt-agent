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
a hint and strict tools drop it), and only simple regexes compile. A schema the
API cannot express at all (a free-form or map-typed object, tuple ``items``)
keeps llmai's previous forced-tool path, with a warning, rather than being
narrowed silently.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator, Iterable, Mapping
from copy import deepcopy
from typing import Any

import llmai.client as llmai_client_module
from llmai.anthropic.client import AnthropicClient
from llmai.shared.messages import TextContentPart
from llmai.shared.response_formats import (
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
    return _transform_schema(schema, "$")


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


def parse_structured_text(content: object) -> object:
    """Return the JSON object in a structured answer, else ``content`` unchanged.

    Output that is not a JSON object (a refusal or a ``max_tokens`` stop can
    end mid-document) stays text, the same as any other provider's malformed
    answer, so the callers' existing parse-retry handling applies.
    """

    text = _content_text(content)
    if text is None or not text.strip():
        return content
    try:
        parsed = json.loads(text)
    except ValueError:
        return content
    return parsed if isinstance(parsed, dict) else content


def _with_parsed_content(result: Any) -> Any:
    parsed = parse_structured_text(result.content)
    if parsed is result.content:
        return result
    return result.model_copy(update={"content": parsed})


def _structured_stream_events(events: Generator[Any, None, None] | Iterable[Any]):
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
                event = _with_parsed_content(event)
            yield event
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()


class RepositoryAnthropicClient(AnthropicClient):
    """llmai's Anthropic client with native structured output."""

    def _native_output_format(
        self, response_format: ResponseFormat | None
    ) -> dict[str, Any] | None:
        if not isinstance(response_format, JSONSchemaResponse):
            # JSONObjectResponse asks for a free-form object, which the
            # structured-output grammar cannot express (objects must be closed).
            return None
        schema = get_response_schema(response_format)
        if not schema:
            return None
        try:
            wire_schema = native_output_schema(schema)
        except NativeSchemaUnsupported as error:
            LOGGER.warning(
                "anthropic_native_structured_output_unavailable name=%s reason=%s; "
                "using llmai's forced response tool, which Claude Opus 5.5, "
                "Claude Sonnet 5.5 and Claude Fable 5.1 reject",
                response_format.name or "response",
                error,
            )
            return None
        return {"type": "json_schema", "schema": wire_schema}

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
        response_format: ResponseFormat | None = None,
        reasoning_effort: Any = None,
        extra_body: dict | None = None,
        **kwargs: Any,
    ) -> ResponseContent:
        output_format = self._native_output_format(response_format)
        if output_format is None:
            return super()._generate_once(
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
                **kwargs,
            )
        result = super()._generate_once(
            response_format=None,
            reasoning_effort=reasoning_effort,
            extra_body=self._native_extra_body(extra_body, reasoning_effort, output_format),
            **kwargs,
        )
        return _with_parsed_content(result)

    def _generate_stream(
        self,
        *,
        response_format: ResponseFormat | None = None,
        reasoning_effort: Any = None,
        extra_body: dict | None = None,
        **kwargs: Any,
    ):
        output_format = self._native_output_format(response_format)
        if output_format is None:
            return super()._generate_stream(
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                extra_body=extra_body,
                **kwargs,
            )
        return _structured_stream_events(
            super()._generate_stream(
                response_format=None,
                reasoning_effort=reasoning_effort,
                extra_body=self._native_extra_body(
                    extra_body, reasoning_effort, output_format
                ),
                **kwargs,
            )
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
