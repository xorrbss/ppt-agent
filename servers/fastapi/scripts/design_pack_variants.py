"""Draft and approve per-canvas design-pack variants.

docs/aspect-ratio-expansion-design-20260928.md decision 9 / section 5: a pack
serves another canvas only through a separately approved variant.

  draft   --pack <id> --version <semver> --canvas <canvas-id>
          one deck-level LLM re-layout of the base pack onto the target canvas;
          writes variants/<canvas-id>/variant-source.json and a sealed v2
          variant with status "draft" (never used by admission).
  approve --pack <id> --version <semver> --canvas <canvas-id>
          flips the variant to "approved" and reseals its manifest.

The pack store is either ``--storage-root`` or the owner's store under
``APP_DATA_DIRECTORY`` (``--owner <uuid>``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services.authored_vnext.design_pack.variant_store import (  # noqa: E402
    approve_variant,
    draft_variant,
)


def _storage_root(args: argparse.Namespace) -> Path:
    if args.storage_root:
        return Path(args.storage_root)
    from services.authored_vnext.generation_route import design_pack_storage_root

    return design_pack_storage_root(args.owner)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Design-pack canvas variants")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("draft", "approve"):
        command = commands.add_parser(name)
        command.add_argument("--pack", required=True)
        command.add_argument("--version", required=True)
        command.add_argument("--canvas", required=True)
        store = command.add_mutually_exclusive_group(required=True)
        store.add_argument("--storage-root")
        store.add_argument("--owner")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _storage_root(args)
    identity = {"pack_id": args.pack, "package_version": args.version, "canvas_id": args.canvas}
    if args.command == "draft":
        variant = asyncio.run(draft_variant(root, **identity))
    else:
        variant = approve_variant(root, **identity)
    manifest = variant.manifest
    print(f"{manifest.pack_id}@{manifest.package_version} {variant.canvas_id} {variant.status}")
    print(manifest.package_digest)
    print(variant.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
