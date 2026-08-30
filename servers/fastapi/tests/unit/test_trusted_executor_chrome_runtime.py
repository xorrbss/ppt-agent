from __future__ import annotations

import asyncio
from dataclasses import replace
import os
import pathlib
import signal
import subprocess
import tempfile
import time

import pytest

from services.trusted_executor.chrome_runtime import (
    ChromiumCDPProcess,
    ChromeSandboxPolicy,
    ContextIsolationError,
    DisposableSlideContext,
    EmptyImagePrewarm,
    FilesystemMode,
    NetworkMode,
    OperationBrowserScope,
    OperationChromeRuntime,
    PrewarmRejectedError,
    SandboxEnforcementError,
    SandboxEnforcementReport,
    ScopeIsolationError,
    SlideContextRequest,
    SupervisorKind,
)
from utils.slide_capture_chrome_discovery import find_chrome


SCOPE = OperationBrowserScope(
    operation_id="op-1",
    tenant_id="tenant-1",
    owner_id="owner-1",
    presentation_id="presentation-1",
)


class FakeContext(DisposableSlideContext):
    def __init__(self, context_instance_id: str, request: SlideContextRequest) -> None:
        self.context_instance_id = context_instance_id
        self.request = request
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, enforcement: SandboxEnforcementReport) -> None:
        self.enforcement = enforcement
        self.contexts: list[FakeContext] = []
        self.terminated_with: list[str] = []
        self.remaining_descendants: tuple[int, ...] = ()
        self.reuse_context_id = False
        self.context_scope_override: OperationBrowserScope | None = None

    async def new_context(self, request: SlideContextRequest) -> FakeContext:
        if self.context_scope_override is not None:
            request = replace(request, scope=self.context_scope_override)
        ordinal = 1 if self.reuse_context_id else len(self.contexts) + 1
        context = FakeContext(f"context-{ordinal}", request)
        self.contexts.append(context)
        return context

    async def terminate_tree(self, reason: str) -> None:
        self.terminated_with.append(reason)
        self.remaining_descendants = ()

    async def descendant_pids(self) -> tuple[int, ...]:
        return self.remaining_descendants


class FakeLauncher:
    def __init__(self, reports: list[SandboxEnforcementReport]) -> None:
        self.reports = reports
        self.processes: list[FakeProcess] = []

    async def launch(self, *, scope, policy) -> FakeProcess:
        assert isinstance(policy, ChromeSandboxPolicy)
        report = replace(self.reports[len(self.processes)], scope=scope)
        process = FakeProcess(report)
        self.processes.append(process)
        return process


def _report(*, process_instance_id: str = "chrome-1") -> SandboxEnforcementReport:
    return SandboxEnforcementReport(
        scope=SCOPE,
        process_instance_id=process_instance_id,
        pid=4100,
        root_filesystem=FilesystemMode.READ_ONLY,
        max_tmp_bytes=64 * 1024 * 1024,
        max_tmp_files=512,
        network=NetworkMode.NONE,
        secret_names=(),
        empty_ephemeral_user_data=True,
        supervisor=SupervisorKind.WINDOWS_JOB_OBJECT,
        owns_descendants=True,
    )


def test_operation_reuses_one_process_but_disposes_every_slide_context() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher([_report()])
        runtime = OperationChromeRuntime(launcher)

        first = await runtime.open_operation(SCOPE)
        second = await runtime.open_operation(SCOPE)
        assert first is second
        assert len(launcher.processes) == 1

        async with first.slide_context(0) as slide_zero:
            assert not slide_zero.closed
            assert slide_zero.request.network is NetworkMode.NONE
            assert slide_zero.request.filesystem is FilesystemMode.READ_ONLY
            assert slide_zero.request.secret_names == ()
        async with first.slide_context(1) as slide_one:
            assert not slide_one.closed

        assert slide_zero.closed and slide_one.closed
        assert slide_zero.context_instance_id != slide_one.context_instance_id
        await runtime.close_operation(SCOPE, reason="completed")
        assert launcher.processes[0].terminated_with == ["completed"]
        assert await launcher.processes[0].descendant_pids() == ()

    asyncio.run(scenario())


def test_scope_enforcement_and_process_identity_fail_closed() -> None:
    async def scenario() -> None:
        other_owner = replace(SCOPE, owner_id="owner-2")
        launcher = FakeLauncher([_report(), _report()])
        runtime = OperationChromeRuntime(launcher)
        await runtime.open_operation(SCOPE)

        with pytest.raises(ScopeIsolationError, match="different tenant, owner, or presentation"):
            await runtime.open_operation(other_owner)

        await runtime.close_operation(SCOPE, reason="completed")
        second_scope = replace(SCOPE, operation_id="op-2")
        with pytest.raises(ScopeIsolationError, match="reused a Chrome process"):
            await runtime.open_operation(second_scope)
        assert launcher.processes[1].terminated_with == ["sandbox-enforcement-rejected"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        (replace(_report(), root_filesystem="writable"), "root-filesystem-not-read-only"),
        (replace(_report(), max_tmp_bytes=512 * 1024 * 1024), "tmp-byte-limit-not-enforced"),
        (replace(_report(), network="outbound"), "outbound-network-enabled"),
        (replace(_report(), secret_names=("API_TOKEN",)), "secret-material-present"),
        (replace(_report(), empty_ephemeral_user_data=False), "user-data-not-empty"),
        (replace(_report(), owns_descendants=False), "descendant-ownership-unproven"),
    ],
)
def test_missing_sandbox_enforcement_rejects_and_terminates(report, reason: str) -> None:
    async def scenario() -> None:
        launcher = FakeLauncher([report])
        runtime = OperationChromeRuntime(launcher)
        with pytest.raises(SandboxEnforcementError, match=reason):
            await runtime.open_operation(SCOPE)
        assert launcher.processes[0].terminated_with == ["sandbox-enforcement-rejected"]
        assert await launcher.processes[0].descendant_pids() == ()

    asyncio.run(scenario())


def test_context_reuse_and_cross_owner_context_are_fatal() -> None:
    async def scenario() -> None:
        launcher = FakeLauncher([_report(), _report(process_instance_id="chrome-2")])
        runtime = OperationChromeRuntime(launcher)
        session = await runtime.open_operation(SCOPE)
        launcher.processes[0].reuse_context_id = True
        async with session.slide_context(0):
            pass
        with pytest.raises(ContextIsolationError, match="context-reused"):
            async with session.slide_context(1):
                pass
        assert launcher.processes[0].terminated_with == ["context-isolation-violation"]

        second_scope = replace(SCOPE, operation_id="op-2")
        second = await runtime.open_operation(second_scope)
        launcher.processes[1].context_scope_override = replace(second_scope, owner_id="owner-2")
        with pytest.raises(ContextIsolationError, match="context-request-mismatch"):
            async with second.slide_context(0):
                pass
        assert launcher.processes[1].terminated_with == ["context-isolation-violation"]

    asyncio.run(scenario())


def test_prewarm_allows_only_an_empty_image() -> None:
    OperationChromeRuntime.validate_prewarm(EmptyImagePrewarm(image_id="chrome-worker-v1"))
    with pytest.raises(PrewarmRejectedError, match="user-data-present"):
        OperationChromeRuntime.validate_prewarm(
            EmptyImagePrewarm(image_id="chrome-worker-v1", contains_user_data=True)
        )
    with pytest.raises(PrewarmRejectedError, match="artifacts-staged"):
        OperationChromeRuntime.validate_prewarm(
            EmptyImagePrewarm(image_id="chrome-worker-v1", staged_artifact_count=1)
        )
    with pytest.raises(PrewarmRejectedError, match="secrets-present"):
        OperationChromeRuntime.validate_prewarm(
            EmptyImagePrewarm(image_id="chrome-worker-v1", secret_names=("SESSION",))
        )


def test_real_chromium_cdp_reuses_process_and_disposes_each_browser_context() -> None:
    chrome = find_chrome()
    if chrome is None:
        pytest.skip("Chrome/Chromium is not installed")

    async def scenario() -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            profile = pathlib.Path(directory) / "profile"
            profile.mkdir()
            arguments = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-domain-reliability",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-pings",
                "--remote-debugging-port=0",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ]
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
                start_new_session=os.name != "nt",
            )

            async def terminate_tree(_reason: str) -> None:
                if process.poll() is None:
                    if os.name == "nt":
                        await asyncio.to_thread(
                            subprocess.run,
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)

            async def descendant_pids() -> tuple[int, ...]:
                return () if process.poll() is not None else (process.pid,)

            port_file = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not port_file.is_file():
                if process.poll() is not None:
                    break
                await asyncio.sleep(0.05)
            if not port_file.is_file():
                await terminate_tree("startup-failed")
                pytest.skip("installed Chromium did not expose a DevTools endpoint")

            port, websocket_path = port_file.read_text(encoding="utf-8").splitlines()[:2]
            enforcement = replace(
                _report(process_instance_id=f"real-chromium-{process.pid}"),
                pid=process.pid,
            )
            cdp = await ChromiumCDPProcess.connect(
                enforcement=enforcement,
                browser_websocket_url=f"ws://127.0.0.1:{port}{websocket_path}",
                terminate_tree=terminate_tree,
                descendant_pids=descendant_pids,
            )
            try:
                first = await cdp.new_context(SlideContextRequest(scope=SCOPE, slide_index=0))
                first_context_id = first.context_instance_id
                evaluation = await first.command(
                    "Runtime.evaluate",
                    {"expression": "40 + 2", "returnByValue": True},
                )
                assert evaluation["result"]["value"] == 42
                live = await cdp._command("Target.getBrowserContexts")
                assert first_context_id in live["browserContextIds"]
                await first.close()
                disposed = await cdp._command("Target.getBrowserContexts")
                assert first_context_id not in disposed["browserContextIds"]

                second = await cdp.new_context(SlideContextRequest(scope=SCOPE, slide_index=1))
                assert second.context_instance_id != first_context_id
                await second.close()
            finally:
                await cdp.terminate_tree("real-runtime-test-completed")
            assert await cdp.descendant_pids() == ()

    asyncio.run(scenario())
