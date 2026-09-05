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
  document is returned.

The first two raise :class:`AuthoredStreamAborted`, which is an ordinary
non-terminal provider failure: ``author_deck``'s attempt loop treats it exactly
like invalid HTML, spending the correction it would have spent anyway.

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
from services.provider_usage_instrumentation import current_provider_idempotency_key

logger = logging.getLogger(__name__)

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
    async for event in _stream_events(client, kwargs):
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

        if self.completion is not None and not self.truncated:
            return self.completion
        return StreamedAuthorResponse(content=self._text)


def _content_chunk(event: Any) -> str | None:
    if getattr(event, "type", None) != "content":
        return None
    chunk = getattr(event, "chunk", None)
    return chunk if isinstance(chunk, str) else None


async def _stream_events(client: Any, kwargs: Mapping[str, Any]):
    """Yield provider stream events, pumping the SDK on the author executor.

    ``llmai`` streams synchronously, so the iterator is drained on the bounded
    authoring thread pool and its events cross into the loop through a queue.
    Leaving this generator (an abort, a cancellation, the end of the document)
    sets ``stop_requested``, and the pump then closes the provider iterator
    instead of downloading the rest of the response.
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
    "AuthoredStreamAborted",
    "StreamedAuthorResponse",
    "author_stream_char_budget",
    "is_authored_author_streaming_enabled",
    "stream_author_call",
]
