"""Completed-image adapter for the existing ChatGPT/Codex OAuth session.

Codex currently accepts even unknown requested image model IDs. Receipts must
therefore distinguish the requested model from the unattested effective model.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
from pathlib import Path
import uuid

import httpx

from utils.codex_token_refresh import ensure_fresh_codex_access_token
from utils.get_env import (
    get_codex_account_id_env,
    get_gpt_image_2_5_quality_env,
    get_image_generation_timeout_seconds,
)
from utils.runtime_settings import RuntimeSettings, resolve_runtime_settings

CODEX_IMAGE_REQUESTED_MODEL = "gpt-image-2.5-sunburst"
CODEX_IMAGE_ENDPOINT = "https://chatgpt.com/backend-api/codex/images/generations"


async def generate_codex_image(
    prompt: str, output_directory: str, settings: RuntimeSettings | None = None
) -> str:
    from services.image_generation_service import _metered_image_provider_call

    snapshot = resolve_runtime_settings(settings)
    token = await asyncio.to_thread(ensure_fresh_codex_access_token, snapshot)
    account_id = get_codex_account_id_env(snapshot)
    if not token or not account_id:
        raise ValueError("Codex login is required for image generation")

    headers = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "originator": "codex_cli_rs",
        "Accept": "text/event-stream",
    }
    payload = {
        "model": CODEX_IMAGE_REQUESTED_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",
        "quality": get_gpt_image_2_5_quality_env(snapshot) or "medium",
        "stream": True,
    }
    completed = None
    # A disconnected stream may already have spent subscription allowance.
    # Do not retry it or fall back to a separately billed OpenAI API request.
    async with _metered_image_provider_call(
        provider="codex",
        model="codex-image-default",
        semantic_slot="image.primary.codex",
    ):
        async with httpx.AsyncClient(
            timeout=get_image_generation_timeout_seconds(snapshot),
            follow_redirects=False,
        ) as client:
            async with client.stream("POST", CODEX_IMAGE_ENDPOINT, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"Codex image generation failed (HTTP {response.status_code})")
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise RuntimeError("Codex image generation returned an unexpected response type")
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line and data_lines:
                        data = "\n".join(data_lines)
                        data_lines.clear()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            raise RuntimeError("Codex image generation returned invalid event data") from None
                        if not isinstance(event, dict):
                            raise RuntimeError("Codex image generation returned an invalid event")
                        if event.get("error") or event.get("type") in {"error", "image_generation.failed"}:
                            raise RuntimeError("Codex image generation failed")
                        if event.get("type") == "image_generation.completed":
                            completed = event
                            break
        if completed is None or not isinstance(completed.get("b64_json"), str):
            raise RuntimeError("Codex image generation ended without a completed image")
        try:
            image_bytes = base64.b64decode(completed["b64_json"], validate=True)
        except (ValueError, binascii.Error):
            raise RuntimeError("Codex image generation returned invalid image data") from None
        if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Codex image generation did not return a PNG")

    image_path = Path(output_directory) / f"{uuid.uuid4()}.png"
    image_path.write_bytes(image_bytes)
    effective_model = completed.get("model")
    receipt = {
        "provider": "codex",
        "authentication": "codex_oauth",
        "requested_model": CODEX_IMAGE_REQUESTED_MODEL,
        "effective_model": effective_model if isinstance(effective_model, str) else None,
        "model_verified": isinstance(effective_model, str) and bool(effective_model),
    }
    image_path.with_suffix(".metadata.json").write_text(json.dumps(receipt), encoding="utf-8")
    return str(image_path)
