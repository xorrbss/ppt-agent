#!/usr/bin/env python3
"""Deterministic source/contract attestation for the authored renderer image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CONTRACT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = CONTRACT_ROOT.parents[1]

IMAGE_SOURCES = (
    "Dockerfile",
    "contracts/authored-renderer/requirements.lock",
    "servers/fastapi/utils/__init__.py",
    "servers/fastapi/utils/authored_renderer_worker.py",
    "servers/fastapi/utils/renderer_isolation.py",
    "servers/fastapi/utils/renderer_internal_capability.py",
    "servers/fastapi/utils/renderer_static_policy.py",
    "servers/fastapi/utils/renderer_worker_policy.py",
    "servers/fastapi/utils/renderer_markup_policy.py",
    "servers/fastapi/utils/renderer_css_policy.py",
    "servers/fastapi/utils/renderer_frame_codec.py",
    "servers/fastapi/utils/renderer_worker_response.py",
    "servers/fastapi/utils/authored_renderer_session.py",
    "servers/fastapi/utils/authored_dom_quality.py",
    "servers/fastapi/utils/authored_dom_categories.py",
    "servers/fastapi/utils/authored_dom_fit_magnitude.py",
    "servers/fastapi/utils/authored_dom_footer_zone.py",
    "servers/fastapi/utils/authored_dom_tags.py",
    "servers/fastapi/utils/authored_dom_text_escape.py",
    "servers/fastapi/utils/authored_qid_namespace.py",
    "servers/fastapi/utils/authored_metrics_cache_policy.py",
    "servers/fastapi/utils/render_cache_budget.py",
    "servers/fastapi/utils/runtime_settings.py",
    "servers/fastapi/utils/get_env.py",
    "servers/fastapi/utils/get_env_programme.py",
    "servers/fastapi/utils/db_utils.py",
    "servers/fastapi/utils/owner_context.py",
    "servers/fastapi/utils/datetime_utils.py",
    "servers/fastapi/utils/parsers.py",
    "servers/fastapi/utils/slide_capture.py",
    "servers/fastapi/utils/slide_capture_assets.py",
    "servers/fastapi/utils/slide_capture_batch.py",
    "servers/fastapi/utils/slide_capture_chrome_discovery.py",
    "servers/fastapi/utils/slide_capture_deck_scope.py",
    "servers/fastapi/utils/slide_capture_glyph_probe.py",
    "servers/fastapi/utils/slide_capture_hit_test.py",
    "servers/fastapi/utils/slide_capture_pipe.py",
    "servers/fastapi/utils/slide_capture_pipe_render.py",
    "servers/fastapi/utils/slide_capture_pixel_probe.py",
    "servers/fastapi/utils/slide_capture_polygon_paint.py",
    "servers/fastapi/utils/slide_capture_reusable_render.py",
    "servers/fastapi/utils/slide_capture_rounded_paint.py",
    "servers/fastapi/utils/slide_capture_worker_client.py",
    "servers/fastapi/utils/slide_capture_worker_lease.py",
    "servers/fastapi/utils/slide_capture_worker_session.py",
    "servers/fastapi/services/__init__.py",
    "servers/fastapi/services/provider_credential_vault.py",
    "servers/fastapi/services/provider_secure_runtime.py",
    "servers/fastapi/services/provider_settings_values.py",
    "servers/fastapi/services/authored_vnext/__init__.py",
    "servers/fastapi/services/authored_vnext/capability_registry.py",
    "servers/fastapi/models/__init__.py",
    "servers/fastapi/models/user_config.py",
    "servers/fastapi/models/sql/provider_credential_reference.py",
    "servers/fastapi/models/authored_vnext/__init__.py",
    "servers/fastapi/models/authored_vnext/deck/__init__.py",
    "servers/fastapi/models/authored_vnext/deck/scene.py",
    "servers/fastapi/models/authored_vnext/master/__init__.py",
    "servers/fastapi/models/authored_vnext/master/base.py",
    "servers/fastapi/models/authored_vnext/master/contract.py",
    "servers/fastapi/constants/__init__.py",
    "servers/fastapi/constants/settings_registry.py",
    # The Next.js boundary is development-only for direct Chrome execution,
    # but its Chrome launch, teardown, input policy, and sandbox contract are
    # security-relevant renderer runtime sources and must invalidate promotion.
    "servers/nextjs/lib/authored-hybrid/browser-source.ts",
    "servers/nextjs/lib/authored-hybrid/chrome-process.ts",
    "servers/nextjs/lib/authored-hybrid/chrome-runner.ts",
    "servers/nextjs/lib/authored-hybrid/chrome-runtime-discovery.ts",
    "servers/nextjs/lib/authored-hybrid/renderer-isolation.ts",
    "servers/nextjs/lib/authored-hybrid/security-data.ts",
    "servers/nextjs/lib/authored-hybrid/security.ts",
)
BOUNDARY_SOURCES = (
    "contracts/authored-renderer/attestation.py",
    "contracts/authored-renderer/contract.json",
    "contracts/authored-renderer/compose.authored-renderer.json",
    "contracts/authored-renderer/chromium-seccomp.json",
    "contracts/authored-renderer/invoke.py",
    "contracts/authored-renderer/run_dynamic_gate.py",
)


def _digest_files(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        payload = (root / relative).read_bytes()
        encoded = relative.replace("\\", "/").encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return "sha256:" + digest.hexdigest()


def source_tree_digest(root: Path = REPOSITORY_ROOT) -> str:
    return _digest_files(root, IMAGE_SOURCES)


def contract_digest(root: Path = REPOSITORY_ROOT) -> str:
    return _digest_files(root, BOUNDARY_SOURCES)


def deployment_attestation(
    image_id: str,
    source_revision: str,
    image_sources: str,
    boundary_contract: str,
) -> str:
    value = json.dumps(
        {
            "contract_digest": boundary_contract,
            "image_id": image_id,
            "source_revision": source_revision,
            "source_tree_digest": image_sources,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", default="")
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--format", choices=("json", "env"), default="json")
    args = parser.parse_args()
    image_sources = source_tree_digest()
    boundary_contract = contract_digest()
    values = {
        "AUTHORED_RENDERER_SOURCE_TREE_DIGEST": image_sources,
        "AUTHORED_RENDERER_CONTRACT_DIGEST": boundary_contract,
    }
    if args.image_id or args.source_revision:
        if not args.image_id or not args.source_revision:
            parser.error("--image-id and --source-revision are required together")
        values["AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION"] = deployment_attestation(
            args.image_id, args.source_revision, image_sources, boundary_contract
        )
    if args.format == "env":
        for name, value in values.items():
            print(f"{name}={value}")
    else:
        print(json.dumps(values, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
