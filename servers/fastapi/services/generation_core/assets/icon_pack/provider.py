"""Transparent-background PNG generation for icon sheets and object cutouts.

The deck's ordinary illustrations go through ``ImageGenerationService`` and
come back opaque, letterboxed to a slide slot. Icons and scene cutouts are a
different product: an alpha-masked PNG the slide composes over its own
background. Verified OpenAI ``gpt-image-*`` models and Codex OAuth images
support alpha output. This module owns their capability and transport decision;
other providers report "unsupported" before spending. Codex output is resampled
to the requested canvas; callers retain their transparency and grid checks.

The request goes through the same admission, receipt and rate-limit handling
the primary illustration call uses; nothing here bypasses provider fencing.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from typing import Literal, Optional

from openai import AsyncOpenAI, RateLimitError
from PIL import Image

from enums.image_provider import ImageProvider
from services.image_generation_codex_adapter import (
    CODEX_IMAGE_REQUESTED_MODEL,
    generate_codex_image,
)
from services.image_generation_service import (
    OPENAI_IMAGE_MAX_ATTEMPTS,
    OPENAI_IMAGE_MAX_WAIT_SECONDS,
    _get_image_generation_lock,
    _metered_image_provider_call,
    _parse_retry_after_seconds,
)
from services.provider_usage_instrumentation import current_provider_idempotency_key
from utils.get_env import (
    get_gpt_image_1_5_quality_env,
    get_gpt_image_2_quality_env,
    get_gpt_image_2_5_quality_env,
    get_openai_api_key_env,
    get_image_generation_timeout_seconds,
    is_parallel_image_generation_enabled,
)
from utils.image_provider import (
    get_selected_image_provider,
    is_image_generation_disabled,
)
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings

TransparentSize = Literal["1024x1024", "1536x1024", "1024x1536"]

#: Providers whose model accepts ``background="transparent"`` and were
#: verified to honour it, or documented by OpenAI (GPT Image 2.5).
#: Keyed by provider, valued by the API model id.
_TRANSPARENT_MODELS: dict[ImageProvider, str] = {
    ImageProvider.GPT_IMAGE_2: "gpt-image-2",
    ImageProvider.GPT_IMAGE_2_5_FLARE: "gpt-image-2.5-flare",
    ImageProvider.GPT_IMAGE_2_5_SUNBURST: "gpt-image-2.5-sunburst",
    ImageProvider.GPT_IMAGE_1_5: "gpt-image-1.5",
    ImageProvider.CODEX_IMAGE: CODEX_IMAGE_REQUESTED_MODEL,
}


class TransparentPngUnsupported(RuntimeError):
    """The selected image provider cannot produce a transparent PNG."""


def transparent_model_for(settings: RuntimeSettings | None = None) -> Optional[str]:
    """The API model id to request a transparent PNG from, or None."""
    try:
        snapshot = resolve_runtime_settings(settings)
        if is_image_generation_disabled(snapshot):
            return None
        provider = get_selected_image_provider(snapshot)
    except Exception:
        # An unknown or malformed provider value is "no alpha output", never
        # an error: the caller only wants to know whether to spend a call.
        return None
    if provider is None:
        return None
    return _TRANSPARENT_MODELS.get(provider)


def _quality_for(model: str, settings: RuntimeSettings | None) -> str:
    if model in {"gpt-image-2.5-flare", "gpt-image-2.5-sunburst"}:
        return get_gpt_image_2_5_quality_env(settings) or "medium"
    if model == "gpt-image-1.5":
        return get_gpt_image_1_5_quality_env(settings) or "medium"
    return get_gpt_image_2_quality_env(settings) or "medium"


class TransparentPngProvider:
    """One-call-at-a-time generator of alpha PNGs on the configured provider.

    ``settings`` is the same runtime snapshot the deck's image service holds,
    so icons bill and fence exactly like the deck's other images.
    """

    def __init__(self, settings: RuntimeSettings | None = None):
        self.settings = resolve_runtime_settings(settings)
        self.model = transparent_model_for(self.settings)

    @property
    def available(self) -> bool:
        return self.model is not None

    async def generate(
        self,
        prompt: str,
        output_directory: str,
        *,
        size: TransparentSize = "1024x1024",
        semantic_slot: str = "image.icon",
    ) -> str:
        """Write one transparent PNG under ``output_directory``; return its path."""
        if self.model is None:
            raise TransparentPngUnsupported("selected image provider has no alpha output")
        if is_parallel_image_generation_enabled(self.settings):
            return await self._generate(prompt, output_directory, size, semantic_slot)
        async with _get_image_generation_lock():
            return await self._generate(prompt, output_directory, size, semantic_slot)

    async def _generate(
        self, prompt: str, output_directory: str, size: str, semantic_slot: str
    ) -> str:
        if get_selected_image_provider(self.settings) == ImageProvider.CODEX_IMAGE:
            # The OAuth adapter owns the single admission/receipt boundary and
            # never retries an uncertain request or falls back to an API key.
            os.makedirs(output_directory, exist_ok=True)
            path = await generate_codex_image(
                prompt,
                output_directory,
                self.settings,
                size=size,
                background="transparent",
                output_format="png",
                semantic_slot=f"{semantic_slot}.codex",
            )
            await asyncio.to_thread(_normalize_dimensions, path, size)
            return path
        api_key = get_openai_api_key_env(self.settings)
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        model = self.model or "gpt-image-2"
        # SDK retries would be unobservable spend outside the admission
        # boundary, exactly as for the primary illustration call.
        client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=get_image_generation_timeout_seconds(self.settings),
        )
        result = None
        for attempt in range(OPENAI_IMAGE_MAX_ATTEMPTS):
            try:
                async with _metered_image_provider_call(
                    is_retry=attempt > 0,
                    semantic_slot=f"{semantic_slot}.attempt-{attempt + 1}",
                    provider="openai",
                    model=model,
                    provider_idempotency_capable=True,
                ):
                    idempotency_key = current_provider_idempotency_key()
                    result = await client.images.generate(
                        model=model,
                        prompt=prompt,
                        n=1,
                        quality=_quality_for(model, self.settings),
                        size=size,
                        background="transparent",
                        output_format="png",
                        extra_headers=(
                            {"Idempotency-Key": idempotency_key}
                            if idempotency_key
                            else None
                        ),
                    )
                break
            except RateLimitError as error:
                if attempt == OPENAI_IMAGE_MAX_ATTEMPTS - 1:
                    raise
                wait = _parse_retry_after_seconds(error)
                if wait is None:
                    wait = min(8.0 * (attempt + 1), OPENAI_IMAGE_MAX_WAIT_SECONDS)
                await asyncio.sleep(min(wait, OPENAI_IMAGE_MAX_WAIT_SECONDS))
        if result is None or not result.data:
            raise RuntimeError("image provider returned no data")
        path = os.path.join(output_directory, f"{uuid.uuid4()}.png")
        await asyncio.to_thread(
            _write_bytes, path, base64.b64decode(result.data[0].b64_json)
        )
        return path


def _normalize_dimensions(path: str, size: str) -> None:
    """Codex may return another canvas size; preserve its whole grid and alpha."""
    expected = tuple(int(value) for value in size.split("x"))
    with Image.open(path) as image:
        if image.size == expected:
            return
        normalized = image.resize(expected, Image.Resampling.LANCZOS)
    normalized.save(path, format="PNG")


def _write_bytes(path: str, payload: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(payload)


__all__ = [
    "TransparentPngProvider",
    "TransparentPngUnsupported",
    "TransparentSize",
    "transparent_model_for",
]
