"""Fail-closed subprocess bridge to the repository OOXML product adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


class OoxmlAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NodeOoxmlProductAdapter:
    """Invoke only the bounded JSON/file product surface, never Office apps."""

    def __init__(
        self,
        *,
        repository_root: Path | None = None,
        node_executable: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.repository_root = (
            repository_root
            or Path(__file__).resolve().parents[5]
        ).resolve()
        self.nextjs_root = self.repository_root / "servers" / "nextjs"
        self.runner = (
            self.nextjs_root / "lib" / "ooxml-vnext" / "product-runner.mjs"
        )
        configured = node_executable or os.getenv("NODE_BINARY") or "node"
        self.node_executable = shutil.which(configured) or configured
        self.timeout_seconds = timeout_seconds

    def _invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.runner.is_file():
            raise OoxmlAdapterError("OOXML_RUNNER_MISSING", str(self.runner))
        with tempfile.TemporaryDirectory(prefix="authored-vnext-ooxml-request-") as temp:
            request_path = Path(temp) / "request.json"
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [
                        self.node_executable,
                        "--experimental-strip-types",
                        str(self.runner),
                        str(request_path),
                    ],
                    cwd=self.nextjs_root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout_seconds,
                    check=False,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                        else 0
                    ),
                )
            except FileNotFoundError as error:
                raise OoxmlAdapterError(
                    "OOXML_NODE_UNAVAILABLE", "Node.js is required for OOXML operations"
                ) from error
            except subprocess.TimeoutExpired as error:
                raise OoxmlAdapterError(
                    "OOXML_RUNNER_TIMEOUT", "OOXML adapter exceeded its bounded timeout"
                ) from error
        try:
            envelope = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as error:
            raise OoxmlAdapterError(
                "OOXML_RUNNER_PROTOCOL_ERROR",
                "OOXML adapter returned no valid JSON receipt",
            ) from error
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            failure = envelope.get("error") if isinstance(envelope, dict) else None
            code = failure.get("code") if isinstance(failure, dict) else None
            message = failure.get("message") if isinstance(failure, dict) else None
            raise OoxmlAdapterError(
                str(code or "OOXML_RUNNER_FAILED"),
                str(message or completed.stderr.strip() or "OOXML adapter failed"),
            )
        receipt = envelope.get("receipt")
        if completed.returncode != 0 or not isinstance(receipt, dict):
            raise OoxmlAdapterError(
                "OOXML_RUNNER_PROTOCOL_ERROR",
                "OOXML adapter success receipt was inconsistent",
            )
        return receipt

    def inspect(self, source: Path) -> dict[str, Any]:
        return self._invoke(
            {
                "schemaVersion": "authored-vnext-ooxml-product-runner/v1",
                "operation": "INSPECT",
                "inputPath": str(source.resolve()),
            }
        )

    def preserve_save(
        self,
        source: Path,
        destination: Path,
        *,
        patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "schemaVersion": "authored-vnext-ooxml-product-runner/v1",
            "operation": "PRESERVE_SAVE",
            "inputPath": str(source.resolve()),
            "outputPath": str(destination.resolve()),
        }
        if patch is not None:
            request["patch"] = patch
        return self._invoke(request)

    def generate_save(
        self,
        destination: Path,
        *,
        master_contract: dict[str, Any],
        slides: list[dict[str, Any]],
        delete_master: bool = False,
        assets: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self._invoke(
            {
                "schemaVersion": "authored-vnext-ooxml-product-runner/v1",
                "operation": "GENERATE_SAVE",
                "outputPath": str(destination.resolve()),
                "masterContract": master_contract,
                "slides": slides,
                "deleteMaster": delete_master,
                "assets": assets or [],
            }
        )


__all__ = ["NodeOoxmlProductAdapter", "OoxmlAdapterError"]
