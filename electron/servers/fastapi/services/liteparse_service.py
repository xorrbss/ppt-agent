import json
import os
import subprocess
from typing import Any, Dict, Tuple


class LiteParseError(Exception):
    pass


class LiteParseService:
    def __init__(self, timeout_seconds: int = 180):
        self.timeout_seconds = timeout_seconds
        self.node_binary = os.getenv("LITEPARSE_NODE_BINARY", "node")
        self.runner_path = os.getenv("LITEPARSE_RUNNER_PATH", self._resolve_runner_path())
        self.runner_dir = os.path.dirname(self.runner_path)
        self._npm_project_root = self._resolve_npm_project_root()

    def _resolve_npm_project_root(self) -> str:
        """Directory whose node_modules contains @llamaindex/liteparse (runner dir or Electron app root)."""
        local_nm = os.path.join(
            self.runner_dir, "node_modules", "@llamaindex", "liteparse"
        )
        if os.path.isdir(local_nm):
            return self.runner_dir
        electron_nm = os.path.abspath(
            os.path.join(self.runner_dir, "..", "..", "node_modules", "@llamaindex", "liteparse")
        )
        if os.path.isdir(electron_nm):
            return os.path.abspath(os.path.join(self.runner_dir, "..", ".."))
        return os.path.abspath(os.path.join(self.runner_dir, "..", ".."))

    @staticmethod
    def _resolve_runner_path() -> str:
        cwd = os.path.abspath(".")
        candidates = [
            # electron/servers/fastapi → electron/resources/...
            os.path.abspath(
                os.path.join(
                    cwd, "..", "..", "resources", "document-extraction", "liteparse_runner.mjs"
                )
            ),
            # servers/fastapi (repo root layout) → electron/resources/...
            os.path.abspath(
                os.path.join(
                    cwd,
                    "..",
                    "..",
                    "electron",
                    "resources",
                    "document-extraction",
                    "liteparse_runner.mjs",
                )
            ),
            # PyInstaller bundle layout
            os.path.abspath(
                os.path.join(
                    cwd, "..", "..", "app", "resources", "document-extraction", "liteparse_runner.mjs"
                )
            ),
            # Docker / explicit layout
            "/app/document-extraction-liteparse/liteparse_runner.mjs",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return candidates[0]

    def check_runtime_ready(self) -> Tuple[bool, str]:
        if not os.path.isfile(self.runner_path):
            return False, f"LiteParse runner not found at: {self.runner_path}"

        try:
            subprocess.run(
                [self.node_binary, "--version"],
                cwd=self.runner_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            return False, f"Node.js runtime is unavailable: {exc}"

        liteparse_dir = os.path.join(
            self._npm_project_root, "node_modules", "@llamaindex", "liteparse"
        )
        if not os.path.isdir(liteparse_dir):
            return (
                False,
                f"LiteParse npm package missing at {liteparse_dir}. Run npm install in the Electron app directory.",
            )

        # @llamaindex/liteparse is ESM-only; require.resolve() fails. Use dynamic import.
        try:
            subprocess.run(
                [
                    self.node_binary,
                    "--input-type=module",
                    "-e",
                    "import '@llamaindex/liteparse'",
                ],
                cwd=self._npm_project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception as exc:
            return False, f"LiteParse dependency is unavailable: {exc}"

        return True, "ok"

    def parse_to_markdown(
        self,
        file_path: str,
        ocr_enabled: bool = True,
        ocr_language: str = "eng",
    ) -> str:
        result = self.parse(
            file_path=file_path,
            ocr_enabled=ocr_enabled,
            ocr_language=ocr_language,
        )
        return str(result.get("text") or "")

    def parse(
        self,
        file_path: str,
        ocr_enabled: bool = True,
        ocr_language: str = "eng",
    ) -> Dict[str, Any]:
        is_ready, reason = self.check_runtime_ready()
        if not is_ready:
            raise LiteParseError(reason)

        command = [
            self.node_binary,
            self.runner_path,
            "--file",
            file_path,
            "--ocr-enabled",
            "true" if ocr_enabled else "false",
            "--ocr-language",
            ocr_language,
        ]
        ocr_server = (os.getenv("LITEPARSE_OCR_SERVER_URL") or "").strip()
        if ocr_server:
            command.extend(["--ocr-server-url", ocr_server])
        tessdata = (os.getenv("LITEPARSE_TESSDATA_PATH") or "").strip()
        if tessdata:
            command.extend(["--tessdata-path", tessdata])

        process = subprocess.run(
            command,
            cwd=self._npm_project_root,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=os.environ.copy(),
        )
        payload = self._decode_runner_output(process.stdout)

        if process.returncode != 0:
            message = payload.get("error") or process.stderr.strip() or "Unknown error"
            raise LiteParseError(message)

        if not payload.get("ok"):
            raise LiteParseError(payload.get("error") or "LiteParse parse failed")

        return payload

    @staticmethod
    def _decode_runner_output(stdout: str) -> Dict[str, Any]:
        raw = (stdout or "").lstrip("\ufeff").strip()
        if not raw:
            raise LiteParseError("LiteParse runner returned empty output")

        # Prefer the last line that parses as JSON (handles stray log lines before our payload).
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        # Single blob without newlines (entire stdout is one JSON object).
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        raise LiteParseError("LiteParse runner returned invalid JSON output")
