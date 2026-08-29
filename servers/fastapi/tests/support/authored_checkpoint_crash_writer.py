"""Owned subprocess that crashes after sealing one immutable checkpoint bundle."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from services import authored_checkpoint_service as checkpoints


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-path", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--slide-index", required=True, type=int)
    parser.add_argument("--revision", required=True, type=int)
    args = parser.parse_args()

    html = "<section>durable html</section>"
    png = b"durable-png"
    metrics = {"revision": args.revision, "visible": True}
    checkpoints.atomic_write(args.render_path, png)
    checkpoints.write_valid_evidence(
        args.render_path,
        attempt_id=args.attempt_id,
        slide_index=args.slide_index,
        revision=args.revision,
        html=html,
        png=png,
        metrics=metrics,
    )
    os._exit(91)


if __name__ == "__main__":
    main()
