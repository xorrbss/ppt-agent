"""Run the FastAPI backend with a deterministic fake LLM client (dev harness).

The fake answers every call the SVG pipeline makes without a provider:
structured calls get a minimal instance of their JSON schema, the outline gets
N recognisable Korean slides, and SVG author calls get a plain SVG page with
that slide's text. It proves the plumbing (generate -> persist -> PPTX
export), not content quality or provider latency. See README.md here.

Never run this against real data: the worker dispatcher below has no sandbox.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading

sys.path.insert(0, os.getcwd())  # run from servers/fastapi
CALLS: list[str] = []
LONG_FILLER = "".join(
    f"\n- 세부 설명 {i}: 업무 자동화 도입은 반복 작업을 줄이고 처리 시간을 단축하며 오류율을 낮추는 효과가 있다." for i in range(1, 8)
)
_LOCK = threading.Lock()


def _log(kind: str) -> None:
    with _LOCK:
        CALLS.append(kind)
    print(f"[fake-llm] {kind}", flush=True)


def _instance(schema, defs, depth=0):
    if depth > 12 or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _instance(defs.get(name, {}), defs, depth + 1)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return _instance(options[0], defs, depth + 1)
    if "allOf" in schema:
        merged = {}
        for part in schema["allOf"]:
            merged.update(part)
        return _instance(merged, defs, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {name: _instance(sub, defs, depth + 1) for name, sub in props.items()}
    if kind == "array":
        count = max(schema.get("minItems", 0), 0)
        return [_instance(schema.get("items", {}), defs, depth + 1) for _ in range(count)]
    if kind == "string":
        value = "검증용 샘플"
        min_length = schema.get("minLength", 0)
        if len(value) < min_length:
            value = value + "가" * (min_length - len(value))
        if "maxLength" in schema:
            value = value[: schema["maxLength"]]
        return value
    if kind == "integer":
        return int(schema.get("minimum", 1))
    if kind == "number":
        return float(schema.get("minimum", 1))
    if kind == "boolean":
        return False
    return None


def _text_of(messages) -> str:
    parts = []
    for message in messages or ():
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts)


def _schema_of(kwargs):
    fmt = kwargs.get("response_format")
    if fmt is None:
        return None
    schema = getattr(fmt, "json_schema", None)
    if schema is None and isinstance(fmt, dict):
        schema = fmt.get("json_schema")
    if isinstance(schema, dict) and "schema" in schema and "type" not in schema:
        schema = schema["schema"]
    return schema


def _outline(schema, prompt: str) -> dict:
    slides_schema = schema["properties"]["slides"]
    match = re.search(r"(\d+)\s*(?:slides|장)", prompt)
    count = slides_schema.get("minItems") or (int(match.group(1)) if match else 5)
    count = min(count, slides_schema.get("maxItems", count))
    item_schema = slides_schema.get("items", {})
    defs = schema.get("$defs", schema.get("definitions", {}))
    slides = []
    for number in range(1, count + 1):
        item = _instance(item_schema, defs) or {}
        filler = LONG_FILLER if os.environ.get("FAKE_OUTLINE_LONG") == "1" else ""
        item["content"] = f"# 검증 슬라이드 {number}\n- 첫째 요점 {number}\n- 둘째 요점 {number}" + filler
        if "title" in item:
            item["title"] = f"검증 슬라이드 {number}"
        slides.append(item)
    payload = _instance(schema, defs) or {}
    payload["slides"] = slides
    return payload


def _svg(prompt: str) -> str:
    width, height = 1280, 720
    size = re.search(r'viewBox="0 0 (\d+) (\d+)"', prompt) or re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", prompt)
    if size:
        width, height = int(size.group(1)), int(size.group(2))
    title = re.search(r"검증 슬라이드 (\d+)", prompt)
    number = title.group(1) if title else "1"
    lines = [f"검증 슬라이드 {number}", f"첫째 요점 {number}", f"둘째 요점 {number}"]
    texts = "".join(
        f'<text x="{int(width * 0.08)}" y="{int(height * (0.22 + 0.16 * i))}" '
        f'font-family="Noto Sans KR" font-size="{44 if i == 0 else 30}" '
        f'font-weight="{700 if i == 0 else 400}" fill="#1b2230">{line}</text>'
        for i, line in enumerate(lines)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="#ffffff"/>{texts}</svg>'
    )


class FakeTransport:
    """Stands in for the SDK's raw client: callers clone it with options."""

    def with_options(self, **_options):
        return self


class FakeClient:
    def __init__(self, config=None, logger=None):
        self.config = config
        self._client = FakeTransport()

    def generate(self, **kwargs):
        prompt = _text_of(kwargs.get("messages"))
        schema = _schema_of(kwargs)
        if schema is not None and "slides" in schema.get("properties", {}):
            _log("outline")
            content = _outline(schema, prompt)
        elif schema is not None:
            name = getattr(kwargs.get("response_format"), "name", "structured")
            _log(f"structured:{name}")
            content = _instance(schema, schema.get("$defs", schema.get("definitions", {})))
        elif "<svg" in prompt or "SVG" in prompt:
            _log("svg_author")
            content = _svg(prompt)
        else:
            _log("text")
            content = "검증용 응답"
        from llmai.shared import (
            ResponseContent, ResponseStreamCompletionChunk, ResponseStreamContentChunk, ResponseUsage,
        )

        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        usage = ResponseUsage(input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(text) // 4),
                              total_tokens=max(1, len(prompt) // 4) + max(1, len(text) // 4))
        if kwargs.get("stream"):
            return iter([ResponseStreamContentChunk(chunk=text),
                         ResponseStreamCompletionChunk(content=content, usage=usage)])
        return ResponseContent(content=content, usage=usage)


class SubprocessDispatcher:
    """Harness-only stand-in for the sandboxed worker dispatcher.

    The real trusted executor needs Windows job objects (windows-local), Linux
    cgroup v2, or a container runtime. Where none exists (e.g. a cloud dev
    VM), this runs the same worker command as a plain child process with the
    same payload and environment, without any isolation.
    """

    async def execute(self, *, job, presentation_id, payload, command, environment):
        import asyncio as _asyncio
        from services.trusted_executor.worker_dispatcher import TrustedWorkerWireResult

        print(f"[harness-dispatcher] role={getattr(job, 'role', '?')} argv={command}", flush=True)
        process = await _asyncio.create_subprocess_exec(
            *command, stdin=_asyncio.subprocess.PIPE, stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            # The real executor mounts staging roots; the harness shares the controller's store.
            env={**dict(environment), "PUBLIC_WORKER_OBJECT_STORE_ROOT": os.environ["PUBLIC_WORKER_OBJECT_STORE_ROOT"],
                 "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES": "2147483648",
                 "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS": "3600",
                 "PPT_WORKER_OUTPUT_LIMIT_BYTES": "536870912"},
            cwd=os.getcwd(),
        )
        stdout, stderr = await process.communicate(payload)
        if stderr:
            print("[harness-dispatcher] stderr:", stderr.decode("utf-8", "replace")[-2000:], flush=True)
        return TrustedWorkerWireResult(stdout=stdout, exit_code=process.returncode or 0)


def main() -> None:
    import llmai
    from services.isolated_worker_process import install_trusted_worker_dispatcher

    install_trusted_worker_dispatcher(SubprocessDispatcher())

    # FAKE_LLM_CLIENTS=0 keeps the real provider clients, so the same harness
    # (worker dispatcher and settings) runs a real-provider smoke test.
    if os.environ.get("FAKE_LLM_CLIENTS", "1") != "0":
        llmai.get_client = lambda config=None, logger=None, **_kw: FakeClient(config, logger)
    elif os.environ.get("FAKE_LLM_LOG_PROVIDER_ERRORS") == "1":
        # The app records only bounded diagnostics for provider failures; for a
        # smoke run, print the provider's own error message (never headers).
        import llmai.anthropic.client as anthropic_client

        original = anthropic_client.raise_llm_error

        def logged(exc, *args, **kwargs):
            print(f"[provider-error] {type(exc).__name__}: {str(exc)[:600]}", flush=True)
            return original(exc, *args, **kwargs)

        anthropic_client.raise_llm_error = logged
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="warning")


if __name__ == "__main__":
    main()
