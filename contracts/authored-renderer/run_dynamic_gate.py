#!/usr/bin/env python3
"""Run the disposable authored renderer OCI gate without a host fallback."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

CONTRACT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = CONTRACT_ROOT.parents[1]
EXPERIMENT_ROOT = (
    REPOSITORY_ROOT / "docs" / "experiments" / "authored-vnext" / "04-renderer-security"
)
HARNESS_ROOT = EXPERIMENT_ROOT / "harness"
COMPOSE_FILE = CONTRACT_ROOT / "compose.authored-renderer.json"
SECCOMP_PROFILE = CONTRACT_ROOT / "chromium-seccomp.json"
PRODUCT_IMAGE = "presenton-authored-render-worker:local"
HARNESS_IMAGE = "authored-renderer-security-harness:local"


def _run(
    command: list[str], *, env: dict[str, str] | None = None, input: bytes | None = None,
    timeout: int = 900, check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        shell=False,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace")[-4000:]
        raise RuntimeError(f"{command[0]} failed with {completed.returncode}: {message}")
    return completed


def _load_attestation_module() -> Any:
    specification = importlib.util.spec_from_file_location(
        "authored_renderer_attestation", CONTRACT_ROOT / "attestation.py"
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load renderer attestation module")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_electron_executable(value: str) -> Path:
    if os.name != "nt" and len(value) >= 3 and value[1:3] in (":\\", ":/"):
        drive = value[0].lower()
        tail = value[3:].replace("\\", "/")
        return Path(f"/mnt/{drive}/{tail}")
    return Path(value)


def _verify_electron_package(
    package_path: Path, observation_path: Path
) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    if package.get("schema_version") != "authored-electron-probe-package/v2":
        raise RuntimeError("Electron package provenance schema is unsupported")
    provenance = package.get("policy_provenance")
    if not isinstance(provenance, dict) or provenance.get("schema") != (
        "presenton.authored-renderer-policy-provenance/v1"
    ):
        raise RuntimeError("Electron policy provenance is missing")
    expected_paths = {
        "source": "app/utils/authored-renderer-policy.ts",
        "compiled": "app_dist/utils/authored-renderer-policy.js",
        "tsconfig": "tsconfig.json",
    }
    for name, relative in expected_paths.items():
        binding = provenance.get("bindings", {}).get(name, {})
        source = REPOSITORY_ROOT / "electron" / relative
        if binding.get("path") != relative or binding.get("sha256") != _sha256(source):
            raise RuntimeError(f"Electron policy {name} provenance is stale")
    probe = REPOSITORY_ROOT / "electron" / "scripts" / "authored-renderer-runtime-probe.cjs"
    if package.get("probe_source_sha256") != _sha256(probe):
        raise RuntimeError("Electron packaged probe source provenance is stale")
    if package.get("policy_sha256") != provenance["bindings"]["compiled"]["sha256"]:
        raise RuntimeError("Electron compiled policy provenance is inconsistent")
    local_manifest = REPOSITORY_ROOT / "electron" / "app_dist" / (
        ".authored-renderer-policy-provenance.json"
    )
    manifest_digest = _sha256(local_manifest)
    if package.get("policy_provenance_manifest_sha256") != manifest_digest:
        raise RuntimeError("Electron policy provenance manifest is stale")
    if observation.get("policy_provenance_manifest_sha256") != manifest_digest:
        raise RuntimeError("Electron observation did not load the attested policy manifest")
    executable = _portable_electron_executable(package.get("executable", ""))
    if not executable.is_file() or package.get("executable_sha256") != _sha256(executable):
        raise RuntimeError("Electron packaged executable provenance is stale")
    if observation.get("executable_sha256") != package.get("executable_sha256"):
        raise RuntimeError("Electron observation executable does not match the package")
    controls = observation.get("controls")
    if not isinstance(controls, dict) or controls.get(
        "compiled_policy_matches_build_provenance"
    ) is not True:
        raise RuntimeError("Electron packaged policy provenance was not observed")
    return {
        "schema_version": package["schema_version"],
        "executable_sha256": package["executable_sha256"],
        "policy_source_sha256": provenance["bindings"]["source"]["sha256"],
        "policy_compiled_sha256": provenance["bindings"]["compiled"]["sha256"],
        "policy_provenance_manifest_sha256": manifest_digest,
        "probe_source_sha256": package["probe_source_sha256"],
    }


def _revision(docker: str, configured: str) -> str:
    if configured:
        value = configured
    else:
        value = _run(["git", "rev-parse", "HEAD"]).stdout.decode().strip()
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("source revision must be a full lowercase Git commit")
    _run([docker, "version"], timeout=30)
    return value


def _remove(docker: str, name: str) -> None:
    _run([docker, "rm", "--force", name], timeout=30, check=False)


def _product_readiness(docker: str, container: str, timeout: float = 30.0) -> dict[str, Any]:
    """Wait until the fixed worker is blocked on its request pipe, not importing."""

    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    command = (
        'for p in $(cat /proc/1/task/1/children); do '
        'cmd=$(tr "\\000" " " < /proc/$p/cmdline 2>/dev/null); '
        'case "$cmd" in *utils.authored_renderer_worker*) '
        'printf "%s " "$p"; cat /proc/$p/wchan; exit 0;; esac; '
        'done; exit 1'
    )
    while time.monotonic() - started < timeout:
        inspected = json.loads(_run([docker, "inspect", container], timeout=10).stdout)[0]
        state = inspected.get("State", {})
        probe = _run(
            [docker, "exec", container, "/bin/sh", "-c", command],
            timeout=10,
            check=False,
        )
        detail = probe.stdout.decode("utf-8", "replace").strip()
        sample = {
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "container_running": state.get("Running") is True,
            "oom_killed": state.get("OOMKilled") is True,
            "worker_wait": detail,
            "probe_exit_code": probe.returncode,
        }
        samples.append(sample)
        if sample["container_running"] and probe.returncode == 0 and "pipe_read" in detail:
            return {
                "ready": True,
                "criterion": "worker-process-blocked-on-request-pipe",
                "elapsed_ms": sample["elapsed_ms"],
                "samples": samples[-12:],
            }
        time.sleep(0.25)
    return {
        "ready": False,
        "criterion": "worker-process-blocked-on-request-pipe",
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "samples": samples[-12:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--electron-observation", type=Path)
    parser.add_argument("--electron-package-manifest", type=Path)
    parser.add_argument("--source-revision", default="")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    if os.environ.get("AUTHORED_VNEXT_RENDERER_ENABLED", "").strip().lower() != "true":
        print(
            "AUTHORED_VNEXT_RENDERER_ENABLED=true is required for the explicit release gate.",
            file=sys.stderr,
        )
        return 2

    docker = shutil.which("docker")
    if docker is None:
        print("Docker is required; no host fallback is allowed.", file=sys.stderr)
        return 2
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    lifecycle_path = output / "lifecycle-observation.json"
    lifecycle: dict[str, Any] = {
        "schema_version": "authored-renderer-lifecycle/v1",
        "started_monotonic": time.monotonic(),
        "authored_renderer_explicitly_enabled": True,
    }
    product_container = f"authored-renderer-contract-{uuid.uuid4().hex}"
    harness_container = f"authored-renderer-security-{uuid.uuid4().hex}"
    try:
        source_revision = _revision(docker, args.source_revision)
        attestation = _load_attestation_module()
        source_tree = attestation.source_tree_digest()
        contract_digest = attestation.contract_digest()
        environment = dict(os.environ)
        environment.update({
            "AUTHORED_RENDERER_IMAGE_REF": PRODUCT_IMAGE,
            "AUTHORED_RENDERER_SOURCE_REVISION": source_revision,
            "AUTHORED_RENDERER_SOURCE_TREE_DIGEST": source_tree,
            "AUTHORED_RENDERER_CONTRACT_DIGEST": contract_digest,
        })
        build_started = time.monotonic()
        if not args.skip_build:
            _run([
                docker, "compose", "--ansi", "never", "-f", str(COMPOSE_FILE),
                "build", "authored-render-worker",
            ], env=environment, timeout=1200)
            _run([
                docker, "build", "--tag", HARNESS_IMAGE,
                "--build-arg", f"SOURCE_REVISION={source_revision}",
                "--file", str(HARNESS_ROOT / "Dockerfile"), str(EXPERIMENT_ROOT),
            ], timeout=1200)
        lifecycle["build_elapsed_ms"] = round(
            (time.monotonic() - build_started) * 1000, 3
        )
        harness_image_id = _run([
            docker, "image", "inspect", HARNESS_IMAGE, "--format", "{{.Id}}",
        ], timeout=30).stdout.decode().strip()
        if not harness_image_id.startswith("sha256:") or len(harness_image_id) != 71:
            raise RuntimeError("security harness image ID is invalid")
        lifecycle["harness_attestation"] = {
            "image_id": harness_image_id,
            "runtime_sha256": "sha256:" + hashlib.sha256(
                (HARNESS_ROOT / "runtime.mjs").read_bytes()
            ).hexdigest(),
        }
        if args.electron_observation:
            if not args.electron_package_manifest:
                raise RuntimeError(
                    "--electron-package-manifest is required with --electron-observation"
                )
            electron_observation = args.electron_observation.resolve()
            electron_package = args.electron_package_manifest.resolve()
            lifecycle["electron_package_provenance"] = _verify_electron_package(
                electron_package, electron_observation
            )
            evidence_package = (output / "electron-package.json").resolve()
            if electron_package != evidence_package:
                shutil.copyfile(electron_package, evidence_package)

        image_id = _run([
            docker, "image", "inspect", PRODUCT_IMAGE, "--format", "{{.Id}}",
        ], timeout=30).stdout.decode().strip()
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise RuntimeError("product renderer image ID is invalid")
        environment["AUTHORED_RENDERER_IMAGE_REF"] = image_id
        _run([
            docker, "compose", "--ansi", "never", "-f", str(COMPOSE_FILE),
            "run", "-d", "--no-deps", "-T", "--name", product_container,
            "authored-render-worker",
        ], env=environment, timeout=60)
        lifecycle["product_readiness"] = _product_readiness(docker, product_container)
        _atomic_json(lifecycle_path, lifecycle)
        if not lifecycle["product_readiness"]["ready"]:
            raise RuntimeError("product renderer did not reach request-pipe readiness")

        _run([
            docker, "create", "--name", harness_container, "--init",
            "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=96m",
            "--tmpfs", "/dev/shm:rw,noexec,nosuid,nodev,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--security-opt", f"seccomp={SECCOMP_PROFILE}",
            "--pids-limit", "128", "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1", "--ulimit", "nofile=256:256", "--ipc", "private",
            "--user", "65532:65532",
            # Keep the observer itself below the same 128-task cgroup it is
            # verifying; otherwise Node's default pools can starve Chromium and
            # turn a valid sandbox observation into an artificial browser hang.
            "--env", "UV_THREADPOOL_SIZE=1",
            "--env", "NODE_OPTIONS=--v8-pool-size=1",
            "--env", "AUTHORED_VNEXT_RENDERER_ENABLED=true",
            HARNESS_IMAGE,
        ], timeout=60)

        inspect_path = output / "container-inspect.json"
        inspect_value = json.loads(
            _run([docker, "inspect", product_container], timeout=30).stdout
        )
        _atomic_json(inspect_path, inspect_value)

        lifecycle["harness_started_after_product_ready"] = True
        lifecycle["harness_start_monotonic"] = time.monotonic()
        _atomic_json(lifecycle_path, lifecycle)
        runtime = _run(
            [docker, "start", "--attach", harness_container], timeout=180, check=False
        )
        lifecycle["harness_elapsed_ms"] = round(
            (time.monotonic() - lifecycle["harness_start_monotonic"]) * 1000, 3
        )
        harness_state = json.loads(
            _run([docker, "inspect", harness_container], timeout=30).stdout
        )[0].get("State", {})
        lifecycle["harness_exit"] = {
            "exit_code": harness_state.get("ExitCode"),
            "oom_killed": harness_state.get("OOMKilled"),
            "error": harness_state.get("Error", ""),
        }
        _atomic_json(lifecycle_path, lifecycle)
        lines = [line for line in runtime.stdout.decode("utf-8-sig").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("isolated harness produced no observation")
        observation = json.loads(lines[-1])
        observation_path = output / "security-observation.json"
        _atomic_json(observation_path, observation)
        (output / "harness-stderr.txt").write_bytes(runtime.stderr[-65536:])

        inspect_check = _run([
            sys.executable, str(HARNESS_ROOT / "verify_container_inspect.py"),
            "--inspect", str(inspect_path), "--observation", str(observation_path),
            "--source-revision", source_revision,
            "--launcher", str(CONTRACT_ROOT / "invoke.py"), "--image-ref", image_id,
            "--source-tree-digest", source_tree, "--contract-digest", contract_digest,
        ], env=environment, timeout=120, check=False)
        resilience_check = _run([
            sys.executable, str(HARNESS_ROOT / "verify_product_resilience.py"),
            "--observation", str(observation_path),
            "--launcher", str(CONTRACT_ROOT / "invoke.py"),
        ], env=environment, timeout=30, check=False)

        gate_input = observation_path
        if args.electron_observation:
            electron = args.electron_observation.resolve()
            if not electron.is_file():
                raise RuntimeError("Electron observation does not exist")
            gate_input = output / "combined-observation.json"
            _run([
                sys.executable, str(EXPERIMENT_ROOT / "ci" / "combine_observations.py"),
                "--container", str(observation_path), "--electron", str(electron),
                "--output", str(gate_input),
            ], timeout=30)
        gate = _run([
            sys.executable, str(EXPERIMENT_ROOT / "ci" / "security_gate.py"),
            "--input", str(gate_input), "--output-dir", str(output),
        ], timeout=30, check=False)
        (output / "gate-exit-code.txt").write_text(f"{gate.returncode}\n", encoding="ascii")
        if runtime.returncode != 0 or inspect_check.returncode != 0 or resilience_check.returncode != 0:
            return gate.returncode if gate.returncode != 0 else 1
        return gate.returncode
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"dynamic renderer gate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        _remove(docker, harness_container)
        _remove(docker, product_container)


if __name__ == "__main__":
    raise SystemExit(main())
