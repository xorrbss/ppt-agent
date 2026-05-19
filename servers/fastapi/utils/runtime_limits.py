import logging
import os
from typing import Mapping

try:
    import resource
except Exception:  # pragma: no cover - Windows
    resource = None


LOGGER = logging.getLogger(__name__)


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default

    try:
        parsed = int(raw)
    except Exception:
        LOGGER.warning("[runtime_limits] Invalid %s=%r, using default=%s", name, raw, default)
        return default

    return min(max(parsed, minimum), maximum)


def merge_node_options(existing: str | None, max_old_space_mb: int | None) -> str:
    parts = [part for part in (existing or "").split() if part]
    if max_old_space_mb and not any(part.startswith("--max-old-space-size") for part in parts):
        parts.append(f"--max-old-space-size={max_old_space_mb}")
    return " ".join(parts)


def with_node_heap_limit(
    env: Mapping[str, str],
    limit_env_name: str,
    default_mb: int,
    *,
    minimum_mb: int = 256,
    maximum_mb: int = 8192,
) -> dict[str, str]:
    updated = dict(env)
    max_old_space_mb = env_int(limit_env_name, default_mb, minimum_mb, maximum_mb)
    node_options = merge_node_options(updated.get("NODE_OPTIONS"), max_old_space_mb)
    if node_options:
        updated["NODE_OPTIONS"] = node_options
    return updated


def memory_snapshot_mb() -> dict[str, int]:
    if resource is None:
        return {}

    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_kb = int(usage.ru_maxrss)
    if os.sys.platform == "darwin":
        rss_mb = rss_kb // (1024 * 1024)
    else:
        rss_mb = rss_kb // 1024
    return {"rss_mb": max(rss_mb, 0)}


def log_memory(logger: logging.Logger, label: str, **fields: object) -> None:
    logger.info("[memory] %s %s extra=%s", label, memory_snapshot_mb(), fields)
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.add_breadcrumb(
            category="memory",
            message=label,
            level="info",
            data={**memory_snapshot_mb(), **fields},
        )
    except Exception:
        pass


class BoundedTextBuffer:
    def __init__(self, limit: int = 8192):
        self.limit = max(0, limit)
        self._text = ""
        self.truncated_chars = 0

    def append(self, value: bytes | str) -> None:
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = value

        if self.limit <= 0:
            self.truncated_chars += len(text)
            return

        combined = self._text + text
        if len(combined) > self.limit:
            overflow = len(combined) - self.limit
            self.truncated_chars += overflow
            combined = combined[overflow:]
        self._text = combined

    def get(self) -> str:
        text = self._text.strip()
        if self.truncated_chars:
            return f"... [truncated {self.truncated_chars} chars]\n{text}".strip()
        return text


def cap_text_by_env(
    text: str,
    *,
    env_name: str = "PRESENTON_MAX_EXTRACTED_TEXT_CHARS",
    default_chars: int = 500_000,
    logger: logging.Logger | None = None,
    label: str = "text",
) -> str:
    limit = env_int(env_name, default_chars, 1_000, 10_000_000)
    if len(text) <= limit:
        return text

    if logger:
        logger.warning(
            "[runtime_limits] Truncating %s from %s to %s chars",
            label,
            len(text),
            limit,
        )
    return text[:limit]
