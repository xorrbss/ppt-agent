"""Fail-closed lifecycle and CDP adapters for operation-scoped Chromium.

A platform launcher still owns executable discovery and operating-system sandbox
enforcement.  Once that launcher supplies enforcement evidence and a loopback
browser WebSocket, :class:`ChromiumCDPProcess` creates a fresh CDP browser context
and target for every slide.  The process itself is reusable only through the exact
operation/tenant/owner/presentation scope enforced by :class:`OperationChromeRuntime`.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import aiohttp


class ChromeRuntimeError(RuntimeError):
    """Base error for the trusted Chrome runtime boundary."""


class ScopeIsolationError(ChromeRuntimeError):
    """A process or context crossed an operation/tenant/owner boundary."""


class SandboxEnforcementError(ChromeRuntimeError):
    """Required sandbox enforcement could not be proven by the launcher."""


class ContextIsolationError(ChromeRuntimeError):
    """A slide context was not disposable or did not inherit isolation."""


class PrewarmRejectedError(ChromeRuntimeError):
    """A prewarm request contained user data, artifacts, or secrets."""


class CleanupIncompleteError(ChromeRuntimeError):
    """The process supervisor could not prove that the process tree is empty."""


class CDPProtocolError(ChromeRuntimeError):
    """Chromium rejected a CDP command or lost its browser connection."""


class NetworkMode(str, Enum):
    NONE = "none"


class FilesystemMode(str, Enum):
    READ_ONLY = "read-only"


class SupervisorKind(str, Enum):
    LINUX_CGROUP_V2 = "linux-cgroup-v2"
    WINDOWS_JOB_OBJECT = "windows-job-object"


class RuntimeState(str, Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    TAINTED = "tainted"


@dataclass(frozen=True, slots=True)
class OperationBrowserScope:
    """Identity that owns exactly one browser-process lifecycle."""

    operation_id: str
    tenant_id: str
    owner_id: str
    presentation_id: str

    def __post_init__(self) -> None:
        for name in ("operation_id", "tenant_id", "owner_id", "presentation_id"):
            value = getattr(self, name)
            if not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty canonical identifier")


@dataclass(frozen=True, slots=True)
class ChromeSandboxPolicy:
    """Minimum immutable isolation policy accepted by this runtime."""

    root_filesystem: FilesystemMode = FilesystemMode.READ_ONLY
    max_tmp_bytes: int = 256 * 1024 * 1024
    max_tmp_files: int = 4096
    network: NetworkMode = NetworkMode.NONE
    secrets_allowed: bool = False
    empty_ephemeral_user_data: bool = True

    def __post_init__(self) -> None:
        if self.root_filesystem is not FilesystemMode.READ_ONLY:
            raise ValueError("Chrome root filesystem must be read-only")
        if self.max_tmp_bytes <= 0 or self.max_tmp_files <= 0:
            raise ValueError("Chrome temporary storage limits must be positive and bounded")
        if self.network is not NetworkMode.NONE:
            raise ValueError("Chrome outbound network must be disabled")
        if self.secrets_allowed:
            raise ValueError("Chrome sandbox cannot receive secrets")
        if not self.empty_ephemeral_user_data:
            raise ValueError("Chrome user-data must start empty and ephemeral")


@dataclass(frozen=True, slots=True)
class SandboxEnforcementReport:
    """Launcher-supplied evidence; verification is structural, not an OS PASS claim."""

    scope: OperationBrowserScope
    process_instance_id: str
    pid: int
    root_filesystem: FilesystemMode
    max_tmp_bytes: int
    max_tmp_files: int
    network: NetworkMode
    secret_names: tuple[str, ...]
    empty_ephemeral_user_data: bool
    supervisor: SupervisorKind
    owns_descendants: bool

    def verify(self, *, expected_scope: OperationBrowserScope, policy: ChromeSandboxPolicy) -> None:
        failures: list[str] = []
        if self.scope != expected_scope:
            failures.append("scope-mismatch")
        if not self.process_instance_id or self.pid <= 0:
            failures.append("invalid-process-identity")
        if self.root_filesystem is not FilesystemMode.READ_ONLY:
            failures.append("root-filesystem-not-read-only")
        if self.max_tmp_bytes <= 0 or self.max_tmp_bytes > policy.max_tmp_bytes:
            failures.append("tmp-byte-limit-not-enforced")
        if self.max_tmp_files <= 0 or self.max_tmp_files > policy.max_tmp_files:
            failures.append("tmp-file-limit-not-enforced")
        if self.network is not NetworkMode.NONE:
            failures.append("outbound-network-enabled")
        if self.secret_names:
            failures.append("secret-material-present")
        if not self.empty_ephemeral_user_data:
            failures.append("user-data-not-empty")
        if self.supervisor not in {
            SupervisorKind.LINUX_CGROUP_V2,
            SupervisorKind.WINDOWS_JOB_OBJECT,
        }:
            failures.append("unsupported-process-supervisor")
        if not self.owns_descendants:
            failures.append("descendant-ownership-unproven")
        if failures:
            raise SandboxEnforcementError(
                "sandbox enforcement report rejected: " + ", ".join(failures)
            )


@dataclass(frozen=True, slots=True)
class EmptyImagePrewarm:
    """Only a data-free worker image may be prepared before an operation exists."""

    image_id: str
    contains_user_data: bool = False
    staged_artifact_count: int = 0
    secret_names: tuple[str, ...] = ()

    def validate(self) -> None:
        failures: list[str] = []
        if not self.image_id:
            failures.append("missing-image-id")
        if self.contains_user_data:
            failures.append("user-data-present")
        if self.staged_artifact_count != 0:
            failures.append("artifacts-staged")
        if self.secret_names:
            failures.append("secrets-present")
        if failures:
            raise PrewarmRejectedError("prewarm image rejected: " + ", ".join(failures))


@dataclass(frozen=True, slots=True)
class SlideContextRequest:
    scope: OperationBrowserScope
    slide_index: int
    network: NetworkMode = NetworkMode.NONE
    filesystem: FilesystemMode = FilesystemMode.READ_ONLY
    secret_names: tuple[str, ...] = ()
    persistent_user_data: bool = False

    def __post_init__(self) -> None:
        if self.slide_index < 0:
            raise ValueError("slide_index must be zero-based and non-negative")


@runtime_checkable
class DisposableSlideContext(Protocol):
    context_instance_id: str
    request: SlideContextRequest
    closed: bool

    async def close(self) -> None: ...


@runtime_checkable
class SupervisedChromeProcess(Protocol):
    enforcement: SandboxEnforcementReport

    async def new_context(self, request: SlideContextRequest) -> DisposableSlideContext: ...

    async def terminate_tree(self, reason: str) -> None: ...

    async def descendant_pids(self) -> tuple[int, ...]: ...


@runtime_checkable
class ChromeSandboxLauncher(Protocol):
    async def launch(
        self,
        *,
        scope: OperationBrowserScope,
        policy: ChromeSandboxPolicy,
    ) -> SupervisedChromeProcess: ...


_BLOCKED_NETWORK_PATTERNS = (
    "http://*",
    "https://*",
    "file://*",
    "ftp://*",
    "ws://*",
    "wss://*",
)


@dataclass(slots=True)
class ChromiumCDPSlideContext:
    """One disposable CDP BrowserContext containing exactly one slide target."""

    process: ChromiumCDPProcess
    context_instance_id: str
    target_instance_id: str
    session_id: str
    request: SlideContextRequest
    closed: bool = False
    _close_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def command(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Send a page-scoped CDP command while the slide context is active."""

        if self.closed:
            raise CDPProtocolError("slide CDP context is closed")
        return await self.process._command(method, params, session_id=self.session_id)

    async def close(self) -> None:
        async with self._close_lock:
            if self.closed:
                return
            await self.process._dispose_context(self)


class ChromiumCDPProcess:
    """Actual CDP implementation over an already-supervised Chromium process.

    The caller must launch Chromium with a fresh operation-local user-data
    directory and provide truthful OS enforcement evidence.  This adapter never
    upgrades CDP network blocking into an OS sandbox claim; it only adds a second
    fail-closed layer inside the already enforced process boundary.
    """

    def __init__(
        self,
        *,
        enforcement: SandboxEnforcementReport,
        client: aiohttp.ClientSession,
        websocket: aiohttp.ClientWebSocketResponse,
        terminate_tree: Callable[[str], Awaitable[None]],
        descendant_pids: Callable[[], Awaitable[tuple[int, ...]]],
        command_timeout_seconds: float,
    ) -> None:
        self.enforcement = enforcement
        self._client = client
        self._websocket = websocket
        self._terminate_tree_callback = terminate_tree
        self._descendant_pids_callback = descendant_pids
        self._command_timeout_seconds = command_timeout_seconds
        self._command_id = 0
        self._command_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._contexts: dict[str, ChromiumCDPSlideContext] = {}
        self._closing = False
        self._terminated = False

    @classmethod
    async def connect(
        cls,
        *,
        enforcement: SandboxEnforcementReport,
        browser_websocket_url: str,
        terminate_tree: Callable[[str], Awaitable[None]],
        descendant_pids: Callable[[], Awaitable[tuple[int, ...]]],
        command_timeout_seconds: float = 15.0,
    ) -> ChromiumCDPProcess:
        """Connect only to the loopback DevTools endpoint owned by the launcher."""

        parsed = urllib.parse.urlparse(browser_websocket_url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Chrome DevTools WebSocket must use unencrypted loopback ws")
        if command_timeout_seconds <= 0:
            raise ValueError("CDP command timeout must be positive")
        timeout = aiohttp.ClientTimeout(total=command_timeout_seconds)
        client = aiohttp.ClientSession(timeout=timeout)
        try:
            websocket = await client.ws_connect(
                browser_websocket_url,
                max_msg_size=16 * 1024 * 1024,
            )
        except BaseException:
            await client.close()
            raise
        return cls(
            enforcement=enforcement,
            client=client,
            websocket=websocket,
            terminate_tree=terminate_tree,
            descendant_pids=descendant_pids,
            command_timeout_seconds=command_timeout_seconds,
        )

    async def new_context(self, request: SlideContextRequest) -> ChromiumCDPSlideContext:
        if self._closing or self._terminated:
            raise CDPProtocolError("Chromium process is closing or terminated")
        if (
            request.network is not NetworkMode.NONE
            or request.filesystem is not FilesystemMode.READ_ONLY
            or request.secret_names
            or request.persistent_user_data
        ):
            raise ContextIsolationError("slide request weakens the Chromium isolation policy")

        context_id = ""
        target_id = ""
        try:
            created_context = await self._command(
                "Target.createBrowserContext",
                {"disposeOnDetach": True},
            )
            context_id = str(created_context.get("browserContextId") or "")
            if not context_id:
                raise CDPProtocolError("CDP did not return a browser context id")
            created_target = await self._command(
                "Target.createTarget",
                {"url": "about:blank", "browserContextId": context_id},
            )
            target_id = str(created_target.get("targetId") or "")
            if not target_id:
                raise CDPProtocolError("CDP did not return a slide target id")
            attached = await self._command(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = str(attached.get("sessionId") or "")
            if not session_id:
                raise CDPProtocolError("CDP did not return a slide session id")

            context = ChromiumCDPSlideContext(
                process=self,
                context_instance_id=context_id,
                target_instance_id=target_id,
                session_id=session_id,
                request=request,
            )
            await context.command("Network.enable")
            await context.command(
                "Network.setBlockedURLs",
                {"urls": list(_BLOCKED_NETWORK_PATTERNS)},
            )
            await self._command(
                "Browser.setDownloadBehavior",
                {"behavior": "deny", "browserContextId": context_id},
            )
            async with self._lifecycle_lock:
                if self._closing or self._terminated:
                    raise CDPProtocolError("Chromium terminated while creating slide context")
                self._contexts[context_id] = context
            return context
        except BaseException:
            await self._dispose_partial_context(context_id=context_id, target_id=target_id)
            raise

    async def _command(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> Mapping[str, Any]:
        if self._terminated or self._websocket.closed:
            raise CDPProtocolError(f"Chromium CDP is unavailable during {method}")
        async with self._command_lock:
            self._command_id += 1
            command_id = self._command_id
            payload: dict[str, Any] = {
                "id": command_id,
                "method": method,
                "params": dict(params or {}),
            }
            if session_id is not None:
                payload["sessionId"] = session_id
            await self._websocket.send_json(payload)
            try:
                async with asyncio.timeout(self._command_timeout_seconds):
                    while True:
                        message = await self._websocket.receive()
                        if message.type is not aiohttp.WSMsgType.TEXT:
                            raise CDPProtocolError(
                                f"Chromium CDP connection closed during {method}"
                            )
                        response = json.loads(message.data)
                        if response.get("id") != command_id:
                            continue
                        if "error" in response:
                            raise CDPProtocolError(
                                f"Chromium CDP {method} failed: {response['error']}"
                            )
                        result = response.get("result", {})
                        return result if isinstance(result, Mapping) else {}
            except TimeoutError as error:
                raise CDPProtocolError(f"Chromium CDP {method} timed out") from error

    async def _dispose_partial_context(self, *, context_id: str, target_id: str) -> None:
        if target_id:
            try:
                await self._command("Target.closeTarget", {"targetId": target_id})
            except BaseException:
                pass
        if context_id:
            try:
                await self._command(
                    "Target.disposeBrowserContext",
                    {"browserContextId": context_id},
                )
            except BaseException:
                pass

    async def _dispose_context(self, context: ChromiumCDPSlideContext) -> None:
        target_error: BaseException | None = None
        try:
            await self._command(
                "Target.closeTarget",
                {"targetId": context.target_instance_id},
            )
        except BaseException as error:
            target_error = error
        try:
            await self._command(
                "Target.disposeBrowserContext",
                {"browserContextId": context.context_instance_id},
            )
        except BaseException as error:
            raise CleanupIncompleteError("CDP browser context disposal failed") from error
        finally:
            async with self._lifecycle_lock:
                self._contexts.pop(context.context_instance_id, None)
        context.closed = True
        if target_error is not None:
            # Successful BrowserContext disposal closes every contained target;
            # retain the target error only as evidence when disposal also fails.
            return

    async def terminate_tree(self, reason: str) -> None:
        async with self._lifecycle_lock:
            if self._closing or self._terminated:
                return
            self._closing = True
            contexts = tuple(self._contexts.values())
            self._contexts.clear()

        cleanup_errors: list[BaseException] = []
        for context in contexts:
            try:
                await self._dispose_context(context)
            except BaseException as error:
                cleanup_errors.append(error)
        try:
            await self._websocket.close()
        except BaseException as error:
            cleanup_errors.append(error)
        self._terminated = True
        try:
            await self._client.close()
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            await self._terminate_tree_callback(reason)
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            raise CleanupIncompleteError("Chromium CDP/process cleanup failed") from cleanup_errors[0]

    async def descendant_pids(self) -> tuple[int, ...]:
        return await self._descendant_pids_callback()


@dataclass(slots=True)
class OperationChromeSession:
    """One Chrome process reusable only by slides of one exact operation scope."""

    scope: OperationBrowserScope
    policy: ChromeSandboxPolicy
    process: SupervisedChromeProcess
    state: RuntimeState = RuntimeState.ACTIVE
    _contexts: dict[str, DisposableSlideContext] = field(default_factory=dict, init=False)
    _seen_context_ids: set[str] = field(default_factory=set, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @asynccontextmanager
    async def slide_context(self, slide_index: int) -> AsyncIterator[DisposableSlideContext]:
        """Create a fresh slide context and dispose it on every exit path."""

        request = SlideContextRequest(scope=self.scope, slide_index=slide_index)
        rejection: BaseException | None = None
        async with self._lock:
            self._require_active()
            context = await self.process.new_context(request)
            try:
                self._verify_context(context, request)
            except BaseException as exc:
                await self._close_rejected_context(context)
                rejection = exc
            else:
                self._seen_context_ids.add(context.context_instance_id)
                self._contexts[context.context_instance_id] = context

        if rejection is not None:
            await self.close(reason="context-isolation-violation")
            raise rejection

        try:
            yield context
        finally:
            await self._dispose_context(context)

    def _require_active(self) -> None:
        if self.state is not RuntimeState.ACTIVE:
            raise ChromeRuntimeError(f"Chrome session is not active: {self.state.value}")

    def _verify_context(self, context: DisposableSlideContext, request: SlideContextRequest) -> None:
        failures: list[str] = []
        if context.request != request:
            failures.append("context-request-mismatch")
        if not context.context_instance_id:
            failures.append("missing-context-instance-id")
        elif context.context_instance_id in self._seen_context_ids:
            failures.append("context-reused")
        if context.closed:
            failures.append("context-returned-closed")
        if context.request.scope != self.scope:
            failures.append("context-scope-mismatch")
        if context.request.network is not NetworkMode.NONE:
            failures.append("context-network-enabled")
        if context.request.filesystem is not FilesystemMode.READ_ONLY:
            failures.append("context-filesystem-not-read-only")
        if context.request.secret_names:
            failures.append("context-secrets-present")
        if context.request.persistent_user_data:
            failures.append("context-user-data-persistent")
        if failures:
            raise ContextIsolationError("slide context rejected: " + ", ".join(failures))

    async def _close_rejected_context(self, context: DisposableSlideContext) -> None:
        try:
            await context.close()
        except BaseException:
            self.state = RuntimeState.TAINTED

    async def _dispose_context(self, context: DisposableSlideContext) -> None:
        cleanup_error: BaseException | None = None
        try:
            await context.close()
        except BaseException as exc:
            cleanup_error = exc
        async with self._lock:
            self._contexts.pop(context.context_instance_id, None)
            if cleanup_error is not None or not context.closed:
                self.state = RuntimeState.TAINTED
        if cleanup_error is not None or not context.closed:
            await self.close(reason="slide-context-disposal-failed")
            raise CleanupIncompleteError("slide context did not become disposable") from cleanup_error

    async def close(self, *, reason: str) -> None:
        """Terminate the owned process tree and require an empty descendant set."""

        async with self._lock:
            if self.state is RuntimeState.CLOSED:
                return
            self.state = RuntimeState.CLOSING
            contexts = tuple(self._contexts.values())
            self._contexts.clear()

        cleanup_errors: list[BaseException] = []
        for context in contexts:
            try:
                await context.close()
                if not context.closed:
                    cleanup_errors.append(CleanupIncompleteError("context remained open"))
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            await self.process.terminate_tree(reason)
        except BaseException as exc:
            cleanup_errors.append(exc)
        try:
            descendants = await self.process.descendant_pids()
        except BaseException as exc:
            cleanup_errors.append(exc)
            descendants = (-1,)

        if cleanup_errors or descendants:
            self.state = RuntimeState.TAINTED
            detail = f"remaining descendants: {descendants}" if descendants else "process cleanup failed"
            raise CleanupIncompleteError(detail) from (cleanup_errors[0] if cleanup_errors else None)
        self.state = RuntimeState.CLOSED


class OperationChromeRuntime:
    """Coordinates operation-local process reuse without a cross-operation pool."""

    def __init__(
        self,
        launcher: ChromeSandboxLauncher,
        *,
        policy: ChromeSandboxPolicy | None = None,
    ) -> None:
        self._launcher = launcher
        self._policy = policy or ChromeSandboxPolicy()
        self._sessions: dict[str, OperationChromeSession] = {}
        self._used_process_instance_ids: set[str] = set()
        self._lock = asyncio.Lock()

    @staticmethod
    def validate_prewarm(prewarm: EmptyImagePrewarm) -> None:
        prewarm.validate()

    async def open_operation(self, scope: OperationBrowserScope) -> OperationChromeSession:
        async with self._lock:
            current = self._sessions.get(scope.operation_id)
            if current is not None:
                if current.scope != scope:
                    raise ScopeIsolationError(
                        "operation id is already bound to a different tenant, owner, or presentation"
                    )
                current._require_active()
                return current

            process = await self._launcher.launch(scope=scope, policy=self._policy)
            try:
                process.enforcement.verify(expected_scope=scope, policy=self._policy)
                if process.enforcement.process_instance_id in self._used_process_instance_ids:
                    raise ScopeIsolationError("launcher reused a Chrome process across operation lifecycles")
            except BaseException:
                await self._reject_process(process)
                raise

            self._used_process_instance_ids.add(process.enforcement.process_instance_id)
            session = OperationChromeSession(scope=scope, policy=self._policy, process=process)
            self._sessions[scope.operation_id] = session
            return session

    async def close_operation(self, scope: OperationBrowserScope, *, reason: str) -> None:
        async with self._lock:
            session = self._sessions.get(scope.operation_id)
            if session is None:
                return
            if session.scope != scope:
                raise ScopeIsolationError("cannot close an operation owned by another scope")
            self._sessions.pop(scope.operation_id)
        await session.close(reason=reason)

    async def _reject_process(self, process: SupervisedChromeProcess) -> None:
        cleanup_error: BaseException | None = None
        try:
            await process.terminate_tree("sandbox-enforcement-rejected")
        except BaseException as exc:
            cleanup_error = exc
        try:
            descendants = await process.descendant_pids()
        except BaseException as exc:
            raise CleanupIncompleteError("could not inspect rejected Chrome descendants") from exc
        if cleanup_error is not None or descendants:
            raise CleanupIncompleteError(
                f"rejected Chrome process tree is not empty: {descendants}"
            ) from cleanup_error
