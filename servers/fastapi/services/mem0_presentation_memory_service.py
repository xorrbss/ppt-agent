import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional
from uuid import UUID

from services.mem0_oss_memory import get_shared_mem0_client
from utils.env_settings.mem0 import get_mem0_settings
from utils.get_env import get_mem0_timeout_seconds


LOGGER = logging.getLogger(__name__)

# asyncio.wait_for cancelling a to_thread call does not stop the underlying
# worker thread: the mem0 SDK call keeps running to completion (or forever,
# for a hung backend) inside whichever executor it was submitted to.
# asyncio.to_thread submits to the loop's default executor, which every other
# unrelated to_thread caller in this process also shares -- including the
# Chrome discovery check in api/health.py. A stalled mem0 backend would
# therefore slowly consume every worker thread in that shared pool and starve
# callers that never touch mem0 at all. Routing mem0's blocking calls through
# a small, dedicated executor instead means a mem0 stall can only ever
# exhaust mem0's own thread budget.
MEM0_EXECUTOR_MAX_WORKERS = 4
_MEM0_EXECUTOR = ThreadPoolExecutor(
    max_workers=MEM0_EXECUTOR_MAX_WORKERS,
    thread_name_prefix="mem0-presentation-memory",
)


class Mem0PresentationMemoryService:
    def __init__(self):
        mem0 = get_mem0_settings()
        self._enabled = mem0.enabled
        self._runtime_enabled = True
        self._timeout_seconds = get_mem0_timeout_seconds()
        self._top_k = mem0.top_k
        self._max_context_chars = mem0.max_context_chars
        self._namespace_prefix = mem0.presentation_namespace_prefix

    def _scope_user_id(self, presentation_id: UUID) -> str:
        return f"{self._namespace_prefix}:{presentation_id}"

    def _truncate(self, text: str, limit: int = 20000) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit]}\n\n[TRUNCATED]"

    @staticmethod
    def _is_nonfatal_mem0_error(exc: BaseException) -> bool:
        return isinstance(exc, (Exception, SystemExit))

    async def _get_client(self):
        if not self._enabled or not self._runtime_enabled:
            return None
        return get_shared_mem0_client()

    def _disable_runtime(self, reason: str, *, exc: BaseException | None = None) -> None:
        if not self._runtime_enabled:
            return
        self._runtime_enabled = False
        if exc is None:
            LOGGER.warning("Mem0 presentation memory disabled for this process: %s", reason)
            return
        LOGGER.exception(
            "Mem0 presentation memory disabled for this process: %s",
            reason,
            exc_info=exc,
        )

    async def _add_message(self, presentation_id: UUID, message: str):
        client = await self._get_client()
        if client is None or not message.strip():
            return

        scoped_user_id = self._scope_user_id(presentation_id)
        payload = [{"role": "user", "content": self._truncate(message)}]

        def _add():
            try:
                return client.add(payload, user_id=scoped_user_id, infer=False)
            except TypeError:
                return client.add(
                    messages=payload,
                    user_id=scoped_user_id,
                    infer=False,
                )

        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(_MEM0_EXECUTOR, _add),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Mem0 add timed out after %.1fs for presentation_id=%s",
                self._timeout_seconds,
                presentation_id,
            )
        except BaseException as exc:
            if not self._is_nonfatal_mem0_error(exc):
                raise
            if isinstance(exc, SystemExit):
                self._disable_runtime("mem0 runtime failed while adding memory", exc=exc)
                return
            LOGGER.exception(
                "Failed to add mem0 memory for presentation_id=%s", presentation_id
            )

    async def store_generation_context(
        self,
        presentation_id: UUID,
        system_prompt: Optional[str],
        user_prompt: Optional[str],
        extracted_document_text: Optional[str],
        source_content: Optional[str],
        instructions: Optional[str],
    ):
        if source_content:
            await self._add_message(
                presentation_id,
                "[presentation_source_prompt]\n" + source_content,
            )

        if instructions:
            await self._add_message(
                presentation_id,
                "[presentation_generation_instructions]\n" + instructions,
            )

        if system_prompt:
            await self._add_message(
                presentation_id,
                "[outline_system_prompt]\n" + system_prompt,
            )

        if user_prompt:
            await self._add_message(
                presentation_id,
                "[outline_user_prompt]\n" + user_prompt,
            )

        if extracted_document_text:
            await self._add_message(
                presentation_id,
                "[document_extracted_text]\n" + extracted_document_text,
            )

    async def store_generated_outlines(self, presentation_id: UUID, outlines: Any):
        if outlines is None:
            return

        try:
            outlines_text = (
                outlines
                if isinstance(outlines, str)
                else json.dumps(outlines, ensure_ascii=False)
            )
        except Exception:
            outlines_text = str(outlines)

        await self._add_message(
            presentation_id,
            "[generated_outlines]\n" + outlines_text,
        )

    async def store_slide_edit(
        self,
        presentation_id: UUID,
        slide_index: Optional[int],
        edit_prompt: str,
        edited_slide_content: Any,
    ):
        try:
            edited_text = (
                edited_slide_content
                if isinstance(edited_slide_content, str)
                else json.dumps(edited_slide_content, ensure_ascii=False)
            )
        except Exception:
            edited_text = str(edited_slide_content)

        index_text = f"{slide_index}" if slide_index is not None else "unknown"
        message = (
            f"[slide_edit]\n"
            f"slide_index={index_text}\n"
            f"user_edit_prompt={edit_prompt}\n"
            f"edited_slide_content={edited_text}"
        )
        await self._add_message(presentation_id, message)

    async def retrieve_context(self, presentation_id: UUID, query: str) -> str:
        client = await self._get_client()
        if client is None:
            return ""

        scoped_user_id = self._scope_user_id(presentation_id)

        def _search():
            try:
                return client.search(
                    query,
                    filters={"user_id": scoped_user_id},
                    top_k=self._top_k,
                )
            except TypeError:
                return client.search(
                    query,
                    user_id=scoped_user_id,
                    top_k=self._top_k,
                )

        try:
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(_MEM0_EXECUTOR, _search),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Mem0 search timed out after %.1fs for presentation_id=%s",
                self._timeout_seconds,
                presentation_id,
            )
            return ""
        except BaseException as exc:
            if not self._is_nonfatal_mem0_error(exc):
                raise
            if isinstance(exc, SystemExit):
                self._disable_runtime("mem0 runtime failed while searching memory", exc=exc)
                return ""
            LOGGER.exception(
                "Failed to search mem0 context for presentation_id=%s", presentation_id
            )
            return ""

        results = []
        if isinstance(response, dict):
            results = response.get("results") or []
        elif isinstance(response, list):
            results = response

        memories: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            memory_text = item.get("memory") or item.get("text") or item.get("data")
            if not memory_text:
                continue
            normalized = str(memory_text).strip()
            if normalized:
                memories.append(normalized)

        if not memories:
            return ""

        deduped_memories = list(dict.fromkeys(memories))
        context = "\n\n".join(deduped_memories)
        return self._truncate(context, self._max_context_chars)


MEM0_PRESENTATION_MEMORY_SERVICE = Mem0PresentationMemoryService()
