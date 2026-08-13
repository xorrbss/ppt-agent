#!/usr/bin/env python3
"""Host-safe static verification for the production renderer contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]
PROVIDER_ENV = re.compile(
    r"(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|OPENAI|ANTHROPIC|GOOGLE|AZURE|"
    r"BEDROCK|VERTEX|OLLAMA|OPENROUTER|FIREWORKS|TOGETHER|CEREBRAS|LITELLM|"
    r"LMSTUDIO|PEXELS|PIXABAY|TAVILY|BRAVE|EXA|COMFYUI|MEM0)",
    re.IGNORECASE,
)


def _load(name: str) -> dict[str, Any]:
    value = json.loads((ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{name}: top level must be an object")
    return value


def verify() -> list[dict[str, Any]]:
    compose = _load("compose.authored-renderer.json")
    contract = _load("contract.json")
    launcher = (ROOT / "invoke.py").read_text(encoding="utf-8")
    dynamic_runner = (ROOT / "run_dynamic_gate.py").read_text(encoding="utf-8")
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8")
    fastapi_utils = REPOSITORY_ROOT / "servers" / "fastapi" / "utils"
    isolation_source = "\n".join(
        (fastapi_utils / name).read_text(encoding="utf-8")
        for name in (
            "renderer_isolation.py",
            "renderer_internal_capability.py",
            "renderer_static_policy.py",
            "renderer_worker_policy.py",
        )
    )
    worker_source = (
        REPOSITORY_ROOT / "servers" / "fastapi" / "utils" / "authored_renderer_worker.py"
    ).read_text(encoding="utf-8")
    seccomp = _load("chromium-seccomp.json")
    slide_capture_files = (
        "slide_capture.py",
        "slide_capture_batch.py",
        "slide_capture_chrome_discovery.py",
        "slide_capture_pipe.py",
        "slide_capture_pipe_render.py",
        "slide_capture_pixel_probe.py",
        "slide_capture_reusable_render.py",
        "slide_capture_worker_client.py",
    )
    slide_capture_source = "\n".join(
        (fastapi_utils / name).read_text(encoding="utf-8")
        for name in slide_capture_files
    )
    next_isolation_source = (
        REPOSITORY_ROOT
        / "servers"
        / "nextjs"
        / "lib"
        / "authored-hybrid"
        / "renderer-isolation.ts"
    ).read_text(encoding="utf-8")
    attestation_source = (ROOT / "attestation.py").read_text(encoding="utf-8")
    worker = compose["services"][contract["worker"]["service"]]
    checks: list[dict[str, Any]] = []

    def check(check_id: str, condition: Any) -> None:
        passed = bool(condition)
        checks.append({"id": check_id, "pass": passed})
        if not passed:
            raise AssertionError(check_id)

    check("worker-entrypoint", worker["entrypoint"] == [
        "/opt/venv/bin/python", "-m", contract["worker"]["module"]
    ])
    check("worker-module-packaged", (
        (REPOSITORY_ROOT / "servers" / "fastapi" / "utils" / "authored_renderer_worker.py").is_file()
        and "COPY servers/fastapi/utils/authored_renderer_worker.py" in dockerfile
        and "COPY servers/fastapi/utils/renderer_internal_capability.py" in dockerfile
        and "COPY servers/fastapi/utils/renderer_static_policy.py" in dockerfile
        and all(f"COPY servers/fastapi/utils/{name}" in dockerfile for name in slide_capture_files)
    ))
    check("runtime-python-path", (
        "COPY --from=authored-renderer-builder /opt/venv /opt/venv" in dockerfile
        and worker["entrypoint"][0] == "/opt/venv/bin/python"
        and worker["build"].get("target") == "authored-renderer-runtime"
    ))
    check("runtime-chrome-path", (
        "chromium chromium-sandbox" in dockerfile
        and worker["environment"].get("CHROME_PATH") == "/usr/bin/chromium"
    ))
    check("next-renderer-runtime-attested", all(
        f'"servers/nextjs/lib/authored-hybrid/{name}"' in attestation_source
        for name in (
            "browser-source.ts",
            "chrome-process.ts",
            "chrome-runner.ts",
            "chrome-runtime-discovery.ts",
            "renderer-isolation.ts",
            "security-data.ts",
            "security.ts",
        )
    ))
    check("stdin-pipe", worker.get("stdin_open") is True and worker.get("tty") is False)
    check("container-init", worker.get("init") is True)
    check("stdout-artifact", contract["worker"]["response"]["artifact_transport"] == "stdout-base64")
    check("non-root", worker.get("user") == contract["isolation"]["uid_gid"] == "65532:65532")
    check("network-none", worker.get("network_mode") == "none")
    check("no-published-ports", not any(key in worker for key in ("ports", "expose")))
    check("read-only-root", worker.get("read_only") is True)
    check("tmpfs-only", worker.get("tmpfs") == [
        "/tmp:rw,noexec,nosuid,nodev,size=96m,mode=1777",
        "/dev/shm:rw,noexec,nosuid,nodev,size=64m,mode=1777",
    ])
    check("no-host-mount", not any(key in worker for key in ("volumes", "configs", "secrets")))
    check("cap-drop-all", worker.get("cap_drop") == ["ALL"])
    check("no-new-privileges", "no-new-privileges:true" in worker.get("security_opt", []))
    namespace_syscalls = {
        "chroot", "clone", "clone3", "mount", "pivot_root", "setns", "umount2", "unshare",
    }
    check("chromium-seccomp-profile", (
        "seccomp=chromium-seccomp.json" in worker.get("security_opt", [])
        and contract["isolation"].get("seccomp_profile")
        == "contracts/authored-renderer/chromium-seccomp.json"
        and seccomp.get("defaultAction") == "SCMP_ACT_ERRNO"
        and any(
            rule.get("action") == "SCMP_ACT_ALLOW"
            and namespace_syscalls.issubset(set(rule.get("names", [])))
            for rule in seccomp.get("syscalls", [])
        )
        and not any(
            syscall in set(rule.get("names", []))
            and rule.get("action") == "SCMP_ACT_ALLOW"
            for syscall in (
                "bpf", "kexec_load", "kexec_file_load", "init_module", "finit_module", "delete_module",
            )
            for rule in seccomp.get("syscalls", [])
            if not rule.get("includes")
        )
    ))
    check("not-privileged", worker.get("privileged") is not True)
    check("private-ipc", worker.get("ipc") == "private")
    check("resource-limits", (
        worker.get("pids_limit") == 128
        and worker.get("mem_limit") == "512m"
        and worker.get("memswap_limit") == "512m"
        and worker.get("cpus") == 1.0
        and worker.get("ulimits", {}).get("nofile") == {"soft": 256, "hard": 256}
        and contract["isolation"].get("wall_clock_limit_seconds") == 135
        and "render_timeout + STARTUP_GRACE_SECONDS" in launcher
    ))
    environment = worker.get("environment", {})
    check("worker-boundary-marker", environment.get("AUTHORED_RENDERER_WORKER") == "1")
    check("isolated-mode", environment.get("AUTHORED_RENDERER_MODE") == "isolated")
    check("next-production-isolation-attestation", (
        environment.get("AUTHORED_RENDERER_OS_ISOLATION")
        == "nonroot-readonly-tmpfs-network-none"
        == contract["product_flags"]["os_isolation_attestation"]["production_value"]
    ))
    check("next-production-attestation-fail-closed", all(
        token in next_isolation_source for token in (
            'process.env.NODE_ENV === "production"',
            "AUTHORED_RENDERER_OS_ISOLATION",
            "nonroot-readonly-tmpfs-network-none",
            "throw new Error",
        )
    ))
    check("next-production-direct-chrome-forbidden", all(
        token in next_isolation_source for token in (
            "AUTHORED_RENDERER_EXECUTION_BOUNDARY", '"external-worker"',
            "direct local Chrome is forbidden", "!production",
        )
    ))
    check("next-renderer-default-off", (
        'booleanEnvironment("AUTHORED_VNEXT_RENDERER_ENABLED", false)'
        in next_isolation_source
    ))
    check("dynamic-release-gate-explicit-enable", all(
        token in dynamic_runner for token in (
            "AUTHORED_VNEXT_RENDERER_ENABLED",
            "explicit release gate",
            '"--env", "AUTHORED_VNEXT_RENDERER_ENABLED=true"',
        )
    ))
    check("legacy-default-off", environment.get("AUTHORED_RENDERER_LEGACY_ENABLED") == "false")
    check("provider-env-absent", not any(PROVIDER_ENV.search(key) for key in environment))
    check("env-file-absent", "env_file" not in worker)
    check("secret-build-context-excluded", all(
        entry in dockerignore.splitlines() for entry in (".env", ".env.*", "app_data")
    ))
    check("browser-sandbox-not-disabled", not re.search(
        r"--(?:no-sandbox|disable-setuid-sandbox|allow-file-access-from-files)",
        json.dumps(compose) + launcher + worker_source + slide_capture_source,
    ))
    check("product-production-command-required", all(token in isolation_source for token in (
        'deployment_tier == "production"',
        "AUTHORED_RENDERER_WORKER_COMMAND_JSON",
        "os.path.isabs(value[0])",
    )))
    check("product-versioned-wire-aligned", (
        all("authored-render-request/v1" in source for source in (
            isolation_source, worker_source, launcher
        ))
        and all("authored-render-response/v1" in source for source in (
            worker_source, slide_capture_source, launcher
        ))
    ))
    check("product-browser-control-pipe", (
        contract["worker"]["browser_control_transport"] == "chromium-cdp-pipe"
        and '"--remote-debugging-pipe"' in slide_capture_source
        and "_render_admitted_html_over_pipe" in slide_capture_source
        and "_render_admitted_html_in_worker" in worker_source
        and "preexec_fn" not in slide_capture_source
    ))
    check("product-secret-environment-filter", all(token in isolation_source for token in (
        "_WORKER_ENV_ALLOWLIST", "_PROVIDER_ENV_MARKERS", "build_worker_environment"
    )))
    check("product-legacy-dual-opt-in", all(token in isolation_source for token in (
        'mode == "legacy"', "AUTHORED_RENDERER_LEGACY_ENABLED"
    )))
    check("fixed-compose-command", all(token in launcher for token in (
        '"run"', '"--rm"', '"--no-deps"', '"-T"', '"--name"', "shell=False"
    )))
    check("runtime-pull-disabled", worker.get("pull_policy") == "never")
    check("immutable-image-reference-supported", (
        worker.get("image") == "${AUTHORED_RENDERER_IMAGE_REF:-presenton-authored-render-worker:local}"
        and worker.get("build", {}).get("args", {}).get("SOURCE_REVISION")
        == "${AUTHORED_RENDERER_SOURCE_REVISION:-unknown}"
        and "org.opencontainers.image.revision=${SOURCE_REVISION}" in dockerfile
        and worker.get("build", {}).get("args", {}).get("SOURCE_TREE_DIGEST")
        == "${AUTHORED_RENDERER_SOURCE_TREE_DIGEST:-unknown}"
        and worker.get("build", {}).get("args", {}).get("CONTRACT_DIGEST")
        == "${AUTHORED_RENDERER_CONTRACT_DIGEST:-unknown}"
    ))
    check("timeout-removes-only-named-worker", all(token in launcher for token in (
        '"rm"', '"--force"', "container_name", "SIGTERM", "terminate_handler"
    )))
    check("no-runtime-env-forwarding", not re.search(r'"-e"|"--env"|env=os\.environ', launcher))
    check("production-image-attestation", all(token in launcher for token in (
        "AUTHORED_RENDERER_IMAGE_REF", "AUTHORED_RENDERER_SOURCE_REVISION",
        "IMMUTABLE_IMAGE", "SOURCE_REVISION", "renderer-image-attestation-invalid",
        "_docker_environment", "AUTHORED_RENDERER_SOURCE_TREE_DIGEST",
        "AUTHORED_RENDERER_CONTRACT_DIGEST", "AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION",
        "_verify_image_attestation",
    )))
    check("command-json-shell-free", (
        contract["product_flags"]["command"]["format"] == "json-argv-array"
        and contract["product_flags"]["command"]["shell"] is False
        and contract["product_flags"]["command"]["required_in_production"] is True
    ))
    check("no-automatic-legacy-downgrade", contract["failure_policy"]["automatic_legacy_downgrade"] is False)
    check("kill-switch-rejects-new-work", (
        contract["failure_policy"]["new_work_after_kill_switch"] == "reject"
        and "renderer-kill-switch-active" in launcher
    ))
    check("last-known-good-retained", contract["failure_policy"]["retain_last_known_good_artifact"] is True)
    check("static-not-ga-evidence", contract["evidence_policy"]["static_contract_is_ga_evidence"] is False)
    check("04-gate-reused", (REPOSITORY_ROOT / contract["evidence_policy"]["gate"]).is_file())
    check("04-hostile-corpus-reused", (
        REPOSITORY_ROOT / contract["evidence_policy"]["hostile_corpus_verifier"]
    ).is_file())
    check("repository-dynamic-runner", all(token in dynamic_runner for token in (
        '"--network", "none"', '"--read-only"', '"--cap-drop", "ALL"',
        '"no-new-privileges"', 'f"seccomp={SECCOMP_PROFILE}"',
        '"--pids-limit", "128"', '"--memory", "512m"', '"--cpus", "1"',
        '"--ipc", "private"', '"--user", "65532:65532"',
        'combine_observations.py', 'security_gate.py',
    )))
    return checks


def main() -> int:
    try:
        checks = verify()
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({
            "schema_version": "authored-renderer-contract-static-check/v1",
            "status": "FAIL",
            "ga_evidence": False,
            "error": str(exc),
        }, indent=2))
        return 1
    print(json.dumps({
        "schema_version": "authored-renderer-contract-static-check/v1",
        "status": "PASS",
        "ga_evidence": False,
        "dynamic_harness": "NOT_RUN",
        "reason": "Static validation cannot establish runtime containment.",
        "checks": checks,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
