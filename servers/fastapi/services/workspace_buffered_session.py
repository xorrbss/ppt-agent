from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import UpdateBase


class BufferedAsyncSession:
    """Buffer ORM writes so a provider turn never holds the SQL writer lock."""

    def __init__(self, delegate: AsyncSession) -> None:
        self._delegate = delegate
        self.defer_external_chat_memory = True
        self.defer_canonical_chat_persistence = True

    def _retain(self, instance: object) -> None:
        retained = self._delegate.info.setdefault("workspace_chat_provisional_instances", {})
        retained[id(instance)] = instance

    def add(self, instance: object, *, _warn: bool = True) -> None:
        self._retain(instance)
        self._delegate.add(instance, _warn=_warn)

    def add_all(self, instances: list[object]) -> None:
        for instance in instances:
            self._retain(instance)
        self._delegate.add_all(instances)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def commit(self) -> None:
        retained = self._delegate.info.setdefault("workspace_chat_provisional_instances", {})
        for instance in self._delegate.new.union(self._delegate.dirty):
            retained[id(instance)] = instance

    async def flush(self) -> None:
        """Buffer instead of writing until final fenced publication."""

    async def refresh(self, *_args: Any, **_kwargs: Any) -> None:
        """Buffered state is authoritative; a reload would force a flush."""

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(statement, UpdateBase):
            raise RuntimeError(
                "workspace_chat_turn_immediate_dml: route this write through "
                "the ORM unit of work (session.add/session.delete)"
            )
        return await self._delegate.execute(statement, *args, **kwargs)

    async def rollback(self) -> None:
        await self._delegate.rollback()
