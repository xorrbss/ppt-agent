"""Read one authoring call as a stream and stop reading a bad one early.

``docs/authored-quality-speed-programme-design-20260905.md`` section 10 (phase
3, "streaming author call"). The wire is already SSE inside ``llmai``; what the
authored path lacks is any look at the response before the provider has emitted
its last token. A model that answers with prose, or that keeps writing after
``</html>``, still costs the full 10-16k output budget and a full call latency
before ``is_valid_slide_html`` rejects it and the slide spends its one recovery
call.

With ``AUTHORED_AUTHOR_STREAMING_ENABLED`` the same call is consumed as a
stream and cut short on three signals:

* the accumulated text passes the call's own output budget (``max_tokens`` x 4
  characters) -- the model is not going to stop on its own;
* 2 000 characters have arrived with no ``<html``/``<!doctype`` in sight --
  this is prose, not a document;
* ``</html>`` has been seen and 500 further characters followed -- the document
  is complete and the rest is chatter, so reading simply stops and the
  document is returned;
* the provider ended the stream at its output-token ceiling
  (``response.incomplete`` with ``max_output_tokens``, read back through the
  guarded transport's ``terminal_outcome``) and no ``</html>`` ever arrived --
  the document is cut mid-tag, and handing it to renderer admission would only
  report "unclosed elements" for what is really a budget problem.

The first two and the last raise :class:`AuthoredStreamAborted`, which is an
ordinary non-terminal provider failure: ``author_deck``'s attempt loop treats
it exactly like invalid HTML, spending the correction it would have spent
anyway. The ``output_token_limit`` reason is the one the attempt loop, the
author-stage rescue and the deck's failure envelope all name explicitly.

``llmai``'s clients expose streaming through the synchronous
``client.generate(..., stream=True)`` iterator, so -- like every other authored
call -- the provider SDK runs on the bounded authoring executor and its events
are handed to the event loop through a queue. Setting ``stop_requested`` makes
the pump close the iterator, which is what actually stops the download.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from utils.get_env_programme import is_authored_author_streaming_enabled
from utils.provider_idempotency import provider_generate_call
from utils.provider_terminal_outcome import ProviderTerminalOutcome
from services.provider_usage_instrumentation import current_provider_idempotency_key

logger = logging.getLogger(__name__)

#: The abort reason for a document the provider cut at its output ceiling.
OUTPUT_TOKEN_LIMIT_REASON = "output_token_limit"
#: The ``AuthoredProviderBudgetExhausted`` code once both calls ended that way.
OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE = "authored_output_token_limit_budget_exhausted"
#: What the next call is told. There is no rejected candidate to repair: the
#: document was never finished, so the correction is about size, not content.
OUTPUT_TOKEN_LIMIT_CORRECTION_HINT = (
    "The previous response was cut off by the provider's output-token limit "
    "before the document was complete. Return a COMPLETE self-contained HTML "
    "document that ends with </html>, and make it substantially more compact: "
    "fewer and shorter text items, no decorative or repeated markup, no long "
    "inline SVG or data URLs, and CSS reduced to what the layout needs."
)
#: What an operator is told when the budget itself is the cause. The Codex
#: transport rejects ``max_output_tokens`` outright (verified 2026-09-07), so
#: there the ceiling is the model's own and only the slide can get smaller;
#: ``AUTHOR_MAX_TOKENS`` (``author_slide_effort.py``) matters only on a
#: transport that honours it.
OUTPUT_TOKEN_LIMIT_GUIDANCE = (
    "The provider's output-token ceiling cut the slide off before </html>. "
    "Lower the slide density or the reasoning effort so one slide fits the "
    "output budget; on transports that honour max_output_tokens, "
    "AUTHOR_MAX_TOKENS (author_slide_effort.py) can also be raised."
)
#: The authored outcome guard's code for a stream ended by ``response.incomplete``.
_PROVIDER_RESPONSE_INCOMPLETE_CODE = "public_provider_response_incomplete"
# How deep a cause chain is walked for the token-limit abort; the same bound
# the authored failure classifiers use.
_MAX_CAUSE_DEPTH = 6

# Characters allowed before the document must have started.
PROLOGUE_GRACE_CHARS = 2_000
# Characters allowed after ``</html>`` before the rest is treated as chatter.
TRAILING_CHATTER_CHARS = 500
# One output token is worth roughly this many characters of HTML.
CHARS_PER_OUTPUT_TOKEN = 4
# Floor for the derived budget: a call with no declared ``max_tokens`` still
# gets a ceiling, and it is never smaller than a large authored slide.
MIN_OUTPUT_CHARS = 64_000
_DOCUMENT_START = ("<html", "<!doctype")
_DOCUMENT_END = "</html>"


class AuthoredStreamAborted(Exception):
    """One authoring stream was cut short before the provider finished.

    Deliberately not a ``ValueError``: an exact-reconstruction deck treats a
    ``ValueError`` from the author call as terminal, and an aborted stream is
    an ordinary bad response that the slide's next call may fix.
    """

    retryable = True

    def __init__(self, reason: str, *, chars: int = 0) -> None:
        super().__init__(f"authored_stream_aborted:{reason} after {chars} chars")
        self.reason = reason
        self.chars = chars


@dataclass
class StreamedAuthorResponse:
    """The minimal response shape the authored path reads back from a call."""

    content: str
    usage: Any = None


def author_stream_char_budget(kwargs: Mapping[str, Any]) -> int:
    """The character ceiling implied by this call's own output budget."""

    try:
        tokens = int(kwargs.get("max_tokens") or 0)
    except (TypeError, ValueError):
        tokens = 0
    return max(MIN_OUTPUT_CHARS, tokens * CHARS_PER_OUTPUT_TOKEN)


async def stream_author_call(
    client: Any,
    kwargs: Mapping[str, Any],
    *,
    max_output_chars: int | None = None,
) -> Any:
    """Run one authoring call as a stream and return its response.

    Returns the provider's own completion event when the stream ran to the end
    (so the receipt keeps its usage), and a :class:`StreamedAuthorResponse`
    when reading stopped at the end of the document. Raises
    :class:`AuthoredStreamAborted` for a runaway response.

    The caller already holds provider admission and records the call, so this
    adds neither.
    """

    ceiling = max_output_chars or author_stream_char_budget(kwargs)
    reader = _AuthoredStreamReader(ceiling)
    async for event in _stream_events(client, kwargs, reader):
        if getattr(event, "type", None) == "completion":
            reader.completion = event
            continue
        chunk = _content_chunk(event)
        if chunk and reader.consume(chunk):
            break
    return reader.result()


class _AuthoredStreamReader:
    """Accumulates the streamed document and decides when to stop reading."""

    def __init__(self, max_output_chars: int) -> None:
        self._max_output_chars = max_output_chars
        self._text = ""
        self._document_started = False
        self._document_end = -1
        self.completion: Any | None = None
        self.truncated = False
        # Set by the pump once the provider's terminal event was seen; stays
        # ``None`` when reading stopped early or the transport has no
        # Responses resource.
        self.terminal_outcome: ProviderTerminalOutcome | None = None

    def consume(self, chunk: str) -> bool:
        """Add one chunk; ``True`` means stop reading, and keep what we have."""

        self._text += chunk
        if len(self._text) > self._max_output_chars:
            raise AuthoredStreamAborted("output_budget_exceeded", chars=len(self._text))
        lowered = self._text.lower()
        if not self._document_started:
            self._document_started = any(
                marker in lowered for marker in _DOCUMENT_START
            )
            if not self._document_started and len(self._text) > PROLOGUE_GRACE_CHARS:
                raise AuthoredStreamAborted(
                    "no_document_prologue", chars=len(self._text)
                )
        if self._document_end < 0:
            end = lowered.rfind(_DOCUMENT_END)
            if end >= 0:
                self._document_end = end + len(_DOCUMENT_END)
        if self._document_end < 0:
            return False
        if len(self._text) - self._document_end < TRAILING_CHATTER_CHARS:
            return False
        logger.info(
            "authored_stream_trailing_chatter chars=%d",
            len(self._text) - self._document_end,
        )
        self._text = self._text[: self._document_end]
        self.truncated = True
        return True

    def result(self) -> Any:
        """The provider's completion when it arrived, else what was streamed."""

        raise_if_output_token_limited(
            self.terminal_outcome, self._text, document_seen=self._document_end >= 0
        )
        if self.completion is not None and not self.truncated:
            usage = _recovered_usage(self.completion, self.terminal_outcome)
            if usage is None:
                return self.completion
            try:
                self.completion.usage = usage
            except Exception:  # noqa: BLE001 -- a read-only completion shape
                return StreamedAuthorResponse(content=self._text, usage=usage)
            return self.completion
        return StreamedAuthorResponse(content=self._text)


def raise_if_output_token_limited(
    outcome: ProviderTerminalOutcome | None,
    text: str,
    *,
    document_seen: bool | None = None,
) -> None:
    """Refuse a document the provider cut at its output-token ceiling.

    A model may end exactly at the limit, so a ``length`` finish with
    ``</html>`` present is a complete document and is kept. Only ``length``
    with no ``</html>`` anywhere is the truncation this names.
    """

    if outcome is None or not outcome.output_truncated:
        return
    if document_seen is None:
        document_seen = _DOCUMENT_END in text.lower()
    if document_seen:
        return
    raise AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=len(text))


def output_token_limit_from_provider_outcome(
    error: BaseException, outcome: ProviderTerminalOutcome | None
) -> AuthoredStreamAborted | None:
    """Name a guarded transport's ``response.incomplete`` when the ceiling caused it.

    On the chatgpt transport the authored outcome guard raises
    ``public_provider_response_incomplete`` for every incomplete stream before
    the call returns, so ``raise_if_output_token_limited`` never sees the
    document. When the recorded terminal outcome says the reason was the
    output-token limit, that is the same truncation the stream reader names;
    any other incomplete reason keeps the guard's terminal verdict.
    """

    if getattr(error, "code", None) != _PROVIDER_RESPONSE_INCOMPLETE_CODE:
        return None
    if outcome is None or not outcome.output_truncated:
        return None
    return AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON)


def output_token_limit_in_cause_chain(error: object) -> bool:
    """Whether this failure is, or wraps, the output-token-limit abort.

    Recognises both the abort itself and the budget-exhausted code the attempt
    loop derives from it, anywhere in the ``cause``/``__cause__`` chain.
    """

    current: object = error
    visited: set[int] = set()
    for _ in range(_MAX_CAUSE_DEPTH):
        if not isinstance(current, BaseException) or id(current) in visited:
            return False
        visited.add(id(current))
        if isinstance(current, AuthoredStreamAborted):
            return current.reason == OUTPUT_TOKEN_LIMIT_REASON
        if getattr(current, "code", None) == OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE:
            return True
        current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
    return False


def _recovered_usage(completion: Any, outcome: ProviderTerminalOutcome | None) -> Any:
    """The terminal event's usage, only when the completion chunk lost its own.

    ``llmai``'s chatgpt client builds the completion chunk's usage from
    ``response.completed`` alone, so a stream ended by ``response.incomplete``
    arrives with ``usage=None`` even though the terminal event was billed.
    """

    if getattr(completion, "usage", None) is not None or outcome is None:
        return None
    return outcome.usage


def _content_chunk(event: Any) -> str | None:
    if getattr(event, "type", None) != "content":
        return None
    chunk = getattr(event, "chunk", None)
    return chunk if isinstance(chunk, str) else None


async def _stream_events(
    client: Any, kwargs: Mapping[str, Any], reader: _AuthoredStreamReader
):
    """Yield provider stream events, pumping the SDK on the author executor.

    ``llmai`` streams synchronously, so the iterator is drained on the bounded
    authoring thread pool and its events cross into the loop through a queue.
    Leaving this generator (an abort, a cancellation, the end of the document)
    sets ``stop_requested``, and the pump then closes the provider iterator
    instead of downloading the rest of the response.

    When the provider ends the stream itself, the guarded transport's terminal
    outcome is handed to ``reader`` before the generator returns: the pump
    recorded it before it enqueued the sentinel, so the loop-side read is
    ordered after it.
    """

    from utils.llm_calls.author_slide import _AUTHOR_EXECUTOR

    # No ``outcome_scope``: the authored outcome guard inspects the returned
    # object's text, and a stream returns an iterator, not a document. The
    # equivalent judgement is made here instead -- an empty or prose-only
    # stream ends as an invalid-HTML attempt on the caller's own path.
    generate_call = provider_generate_call(
        client,
        provider_id=_provider_id(),
        kwargs={**dict(kwargs), "stream": True},
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

    def pump() -> None:
        events = None
        try:
            events = iter(generate_call())
            for event in events:
                if stop_requested.is_set():
                    break
                enqueue(event)
        except Exception as error:  # noqa: BLE001 -- reported to the consumer
            if not stop_requested.is_set():
                enqueue(error)
        finally:
            close = getattr(events, "close", None)
            if stop_requested.is_set() and callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    logger.debug("authored_stream_close_failed", exc_info=True)
            enqueue(sentinel)

    pump_task = asyncio.ensure_future(loop.run_in_executor(_AUTHOR_EXECUTOR, pump))
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                reader.terminal_outcome = generate_call.terminal_outcome
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop_requested.set()
        if pump_task.done():
            pump_task.exception()
        else:
            pump_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )


def _provider_id() -> str:
    from utils.llm_provider import get_llm_provider

    try:
        return str(get_llm_provider().value)
    except Exception:  # noqa: BLE001 -- offline/test clients have no provider
        return "unknown"


__all__ = [
    "OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE",
    "OUTPUT_TOKEN_LIMIT_CORRECTION_HINT",
    "OUTPUT_TOKEN_LIMIT_GUIDANCE",
    "OUTPUT_TOKEN_LIMIT_REASON",
    "AuthoredStreamAborted",
    "StreamedAuthorResponse",
    "author_stream_char_budget",
    "is_authored_author_streaming_enabled",
    "output_token_limit_from_provider_outcome",
    "output_token_limit_in_cause_chain",
    "raise_if_output_token_limited",
    "stream_author_call",
]
