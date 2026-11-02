"""Renderer identity and runtime binding for public export/render operations.

A public render checkpoint may only be reused by a runtime that would produce
the same pixels.  These helpers resolve that runtime binding, fingerprint the
fonts the owned local worker actually renders with, and hash ordered decks into
the identities persisted alongside checkpoints and review receipts.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import json
import os
import pathlib
from typing import Any, Mapping

from models.sql.public_artifact import normalize_sha256
from services.public_export_errors import PublicExportOperationError
from utils.slide_capture import _render_cache_identity
from utils.hashing import sha256_hex, sha256_prefixed


MAX_PUBLIC_RENDER_OPERATION_SLIDES = 100
PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION = "public-slide-renderer-identity/v1"
PUBLIC_RENDER_WORKER_PROTOCOL_VERSION = "public-worker-protocol/v2"
PUBLIC_RENDER_WORKER_IMAGE_DIGEST_ENV = "PUBLIC_RENDER_WORKER_IMAGE_DIGEST"
PUBLIC_RENDER_WORKER_FONT_DIGEST_ENV = "PUBLIC_RENDER_WORKER_FONT_INVENTORY_DIGEST"
PUBLIC_RENDER_TEST_FONT_DIGEST_ENV = "PUBLIC_RENDER_TEST_FONT_INVENTORY_DIGEST"
_FONT_FILE_SUFFIXES = frozenset({".otc", ".otf", ".ttc", ".ttf", ".woff", ".woff2"})
# Development-only trusted-worker backends whose worker is launched on the
# controller host, so the controller can observe the Chrome and font runtime the
# worker renders with (services/trusted_executor/composition.py).
OWNED_LOCAL_SANDBOX_BACKENDS = frozenset({"windows-local", "linux-local"})
_LINUX_SYSTEM_FONT_ROOTS = ("/usr/share/fonts", "/usr/local/share/fonts")


@lru_cache(maxsize=4096)
def _font_file_content_digest(path: str, size: int, mtime_ns: int) -> str:
    """Hash one immutable-looking font snapshot without re-reading unchanged files."""

    del size, mtime_ns
    digest = sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _local_font_roots(source: Mapping[str, str]) -> list[tuple[str, pathlib.Path]]:
    environment = os.environ
    roots: list[tuple[str, pathlib.Path]] = []
    if os.name == "nt":
        windows_root = (source.get("WINDIR") or environment.get("WINDIR") or "").strip()
        local_app_data = (
            source.get("LOCALAPPDATA") or environment.get("LOCALAPPDATA") or ""
        ).strip()
        if windows_root:
            roots.append(("system", pathlib.Path(windows_root) / "Fonts"))
        if local_app_data:
            roots.append(
                ("user", pathlib.Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
            )
        return roots
    for index, root in enumerate(_LINUX_SYSTEM_FONT_ROOTS):
        roots.append(("system" if index == 0 else f"system-{index}", pathlib.Path(root)))
    home = (source.get("HOME") or environment.get("HOME") or "").strip()
    if home:
        roots.append(("user", pathlib.Path(home) / ".local" / "share" / "fonts"))
    return roots


def _local_font_inventory_digest(source: Mapping[str, str]) -> str:
    """Fingerprint system and per-user fonts used by the owned local worker.

    Windows reads ``%WINDIR%\\Fonts`` and the per-user font folder; Linux reads
    the fontconfig system directories and ``~/.local/share/fonts``.

    File bytes are authoritative.  Size/mtime only key the process-local cache,
    so ordinary installs, removals, and updates invalidate the binding while
    repeated review/export requests avoid re-reading the full font directory.
    A deterministic override is accepted only in the explicit test tier.
    """

    runtime_tier = source.get("ENVIRONMENT", "production").strip().lower()
    test_digest = source.get(PUBLIC_RENDER_TEST_FONT_DIGEST_ENV, "").strip().lower()
    if runtime_tier == "test" and test_digest:
        try:
            return "sha256:" + normalize_sha256(test_digest.removeprefix("sha256:"))
        except ValueError as error:
            raise PublicExportOperationError(
                "public_render_local_font_inventory_unavailable"
            ) from error

    roots = _local_font_roots(source)

    inventory: list[tuple[str, str, int, int, str]] = []
    for root_label, root in roots:
        if not root.is_dir():
            continue
        try:
            candidates = sorted(
                (
                    path
                    for path in root.rglob("*")
                    if path.is_file() and path.suffix.lower() in _FONT_FILE_SUFFIXES
                ),
                key=lambda path: str(path.relative_to(root)).casefold(),
            )
        except OSError as error:
            raise PublicExportOperationError(
                "public_render_local_font_inventory_unavailable"
            ) from error
        for path in candidates:
            try:
                stat = path.stat()
                relative_path = str(path.relative_to(root)).replace("\\", "/").casefold()
                content_digest = _font_file_content_digest(
                    str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns)
                )
            except OSError as error:
                raise PublicExportOperationError(
                    "public_render_local_font_inventory_unavailable"
                ) from error
            inventory.append(
                (
                    root_label,
                    relative_path,
                    int(stat.st_size),
                    int(stat.st_mtime_ns),
                    content_digest,
                )
            )
    if not inventory:
        raise PublicExportOperationError(
            "public_render_local_font_inventory_unavailable"
        )
    payload = json.dumps(
        {
            "schemaVersion": "public-local-font-inventory/v1",
            "files": inventory,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_prefixed(payload)


def public_render_runtime_binding(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve the authority behind a reusable public render checkpoint.

    The owned local worker compositions (Windows Job Object or Linux process
    session) launch the child on the controller host and are explicitly
    development-only. Their identity can therefore reuse
    the controller-observed Chrome/asset contract, but it cannot claim a sealed
    system-font inventory. Any external/production worker must instead supply
    immutable image and font-inventory digests before a checkpoint lookup.
    """

    source = os.environ if environment is None else environment
    backend = source.get("PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND", "").strip().lower()
    local_policy = source.get("PUBLIC_TRUSTED_WORKER_LOCAL_POLICY", "").strip().lower()
    runtime_tier = source.get("ENVIRONMENT", "production").strip().lower()
    owned_local = (
        backend in OWNED_LOCAL_SANDBOX_BACKENDS
        and local_policy == "development"
        and runtime_tier in {"local", "development", "dev", "test"}
    )
    if owned_local:
        return {
            "schemaVersion": "public-render-runtime-binding/v2",
            "mode": "owned-local-controller-observed",
            "checkpointReuseEligible": True,
            "releaseEligible": False,
            "releaseEligibilityReason": "runtime-binding-is-not-release-authorization",
            "workerImageDigest": None,
            "chromeRuntimeDigest": _render_cache_identity(
                "<!doctype html><html><body></body></html>"
            ).chrome_runtime_digest,
            "fontInventoryDigest": _local_font_inventory_digest(source),
            "limitations": [
                "controller-worker-colocation-required",
            ],
        }

    def required_digest(name: str) -> str:
        raw = source.get(name, "").strip().lower()
        if not raw.startswith("sha256:") or raw == "sha256:" + ("0" * 64):
            raise PublicExportOperationError(
                "public_render_worker_runtime_pins_required"
            )
        try:
            normalized = normalize_sha256(raw.removeprefix("sha256:"))
        except ValueError as error:
            raise PublicExportOperationError(
                "public_render_worker_runtime_pins_required"
            ) from error
        return "sha256:" + normalized

    return {
        "schemaVersion": "public-render-runtime-binding/v2",
        "mode": "external-deployment-pinned",
        # The configured pins are controller policy, not a worker result
        # attestation.  Fresh renders remain available, but durable checkpoint
        # reuse stays disabled until the worker echoes and signs both values.
        "checkpointReuseEligible": False,
        "releaseEligible": False,
        "releaseEligibilityReason": "runtime-binding-is-not-release-authorization",
        "chromeRuntimeDigest": None,
        "workerImageDigest": required_digest(
            PUBLIC_RENDER_WORKER_IMAGE_DIGEST_ENV
        ),
        "fontInventoryDigest": required_digest(
            PUBLIC_RENDER_WORKER_FONT_DIGEST_ENV
        ),
        "limitations": ["worker-result-attestation-unavailable"],
    }


def public_slide_renderer_version(
    html: str,
    *,
    runtime_binding: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Bind shared PNG checkpoints to the renderer's complete pixel identity.

    ``_render_cache_identity`` is the renderer's authoritative invalidation
    contract. It covers the resolved Chromium binary, referenced local/inline
    assets, Scene input, and browser measurement version. Hashing that digest
    together with the isolated-worker protocol keeps the persisted field
    compact while making any of those changes miss the shared checkpoint.
    """

    render_identity = _render_cache_identity(html)
    binding = dict(
        runtime_binding
        if runtime_binding is not None
        else public_render_runtime_binding(environment)
    )
    binding_digest = public_render_runtime_binding_digest(binding)
    payload = json.dumps(
        {
            "schemaVersion": PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION,
            "workerProtocolVersion": PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
            "renderCacheIdentity": render_identity.digest,
            "runtimeBinding": binding,
            "runtimeBindingDigest": binding_digest,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        f"{PUBLIC_SLIDE_RENDERER_IDENTITY_VERSION}:"
        f"{sha256_hex(payload)}"
    )


def public_render_runtime_binding_digest(
    runtime_binding: Mapping[str, Any] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Canonical digest shared by review receipts and render checkpoints."""

    binding = dict(
        runtime_binding
        if runtime_binding is not None
        else public_render_runtime_binding(environment)
    )
    payload = json.dumps(
        binding,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_prefixed(payload)


def public_release_review_runtime_binding(
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return an observed review/export runtime shared by the owned local path.

    The current quality reviewer executes on the controller.  It must never
    claim an external worker image/font pin it did not actually render with.
    Until review rendering is moved into that worker, external deployments are
    fail-closed for ``release-v1`` while interactive exports remain separate.
    """

    binding = public_render_runtime_binding(environment)
    if binding.get("mode") != "owned-local-controller-observed":
        raise PublicExportOperationError(
            "public_release_review_runtime_unattested"
        )
    return binding


def public_release_review_deck_render_identity_digest(
    htmls: list[str] | tuple[str, ...],
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Bind an ordered review deck to its actual local Chrome/assets/fonts."""

    if not htmls or len(htmls) > MAX_PUBLIC_RENDER_OPERATION_SLIDES or any(
        not isinstance(html, str) or not html for html in htmls
    ):
        raise PublicExportOperationError(
            "public_release_review_render_identity_invalid"
        )
    binding = public_release_review_runtime_binding(environment)
    return public_deck_render_identity_digest(htmls, runtime_binding=binding)


def public_deck_render_identity_digest(
    htmls: list[str] | tuple[str, ...],
    *,
    runtime_binding: Mapping[str, Any],
) -> str:
    """Hash ordered slide renderer identities under one resolved binding."""

    if not htmls or len(htmls) > MAX_PUBLIC_RENDER_OPERATION_SLIDES or any(
        not isinstance(html, str) or not html for html in htmls
    ):
        raise PublicExportOperationError(
            "public_release_review_render_identity_invalid"
        )
    payload = json.dumps(
        {
            "schemaVersion": "public-release-review-render-identity/v1",
            "runtimeBindingDigest": public_render_runtime_binding_digest(
                runtime_binding
            ),
            "slideRendererVersions": [
                public_slide_renderer_version(html, runtime_binding=runtime_binding)
                for html in htmls
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_prefixed(payload)
