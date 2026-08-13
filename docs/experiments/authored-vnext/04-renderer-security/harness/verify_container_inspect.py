#!/usr/bin/env python3
"""Merge redacted Docker inspect assertions into a renderer observation."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PROVIDER_NAME = re.compile(
    r"(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|OPENAI|ANTHROPIC|GOOGLE|AZURE|"
    r"BEDROCK|VERTEX|OPENROUTER|FIREWORKS|TOGETHER|CEREBRAS|LITELLM|PEXELS|PIXABAY)",
    re.IGNORECASE,
)


def _one(value: Any) -> dict[str, Any]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError("Docker inspect must contain exactly one container")
    return value[0]


def controls(
    inspect: dict[str, Any], expected_revision: str,
    expected_source_tree: str, expected_contract: str,
) -> dict[str, bool]:
    config = inspect.get("Config") or {}
    host = inspect.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    environment_names = {
        str(item).split("=", 1)[0] for item in config.get("Env") or []
    }
    ulimits = {
        item.get("Name"): (item.get("Soft"), item.get("Hard"))
        for item in host.get("Ulimits") or [] if isinstance(item, dict)
    }
    security_opt = {str(item).lower() for item in host.get("SecurityOpt") or []}
    seccomp_values = [
        str(item).split("=", 1)[1]
        for item in host.get("SecurityOpt") or []
        if str(item).startswith("seccomp=")
    ]
    seccomp_profile: dict[str, Any] | None = None
    if len(seccomp_values) == 1:
        try:
            value = json.loads(seccomp_values[0])
            if isinstance(value, dict):
                seccomp_profile = value
        except json.JSONDecodeError:
            seccomp_profile = None
    namespace_syscalls = {
        "chroot", "clone", "clone3", "mount", "pivot_root", "setns", "umount2", "unshare",
    }
    seccomp_rules = seccomp_profile.get("syscalls", []) if seccomp_profile else []
    mounts = inspect.get("Mounts") or []
    tmpfs = host.get("Tmpfs") or {}
    return {
        "image_id_observed": bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(inspect.get("Image") or ""))),
        "source_revision_observed": labels.get("org.opencontainers.image.revision") == expected_revision,
        "source_tree_digest_observed": labels.get(
            "io.presenton.authored-renderer.source-tree-digest"
        ) == expected_source_tree,
        "contract_digest_observed": labels.get(
            "io.presenton.authored-renderer.contract-digest"
        ) == expected_contract,
        "no_host_mounts": (
            not host.get("Binds")
            and all(item.get("Type") == "tmpfs" for item in mounts if isinstance(item, dict))
        ),
        "tmpfs_exact": set(tmpfs) == {"/tmp", "/dev/shm"},
        "no_devices": not host.get("Devices") and not host.get("DeviceRequests") and not host.get("DeviceCgroupRules"),
        "no_provider_secrets": not any(PROVIDER_NAME.search(name) for name in environment_names),
        "no_preload": not any("PRELOAD" in name.upper() for name in environment_names),
        "no_privileged_ipc": host.get("Privileged") is False and host.get("IpcMode") == "private",
        "init_process": host.get("Init") is True,
        "non_root": config.get("User") == "65532:65532",
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "network_none": host.get("NetworkMode") == "none",
        "cap_drop_all": host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"),
        "no_new_privileges": any(value in security_opt for value in ("no-new-privileges", "no-new-privileges:true")),
        "chromium_seccomp_profile": bool(
            seccomp_profile
            and seccomp_profile.get("defaultAction") == "SCMP_ACT_ERRNO"
            and any(
                isinstance(rule, dict)
                and rule.get("action") == "SCMP_ACT_ALLOW"
                and namespace_syscalls.issubset(set(rule.get("names", [])))
                for rule in seccomp_rules
            )
            and not any(
                isinstance(rule, dict)
                and not rule.get("includes")
                and rule.get("action") == "SCMP_ACT_ALLOW"
                and forbidden in set(rule.get("names", []))
                for forbidden in (
                    "bpf", "kexec_load", "kexec_file_load", "init_module", "finit_module", "delete_module",
                )
                for rule in seccomp_rules
            )
        ),
        "resource_limits": (
            host.get("PidsLimit") == 128
            and host.get("Memory") == 536_870_912
            and host.get("MemorySwap") == 536_870_912
            and host.get("NanoCpus") == 1_000_000_000
            and ulimits.get("nofile") == (256, 256)
        ),
        "no_published_ports": not host.get("PortBindings") and not config.get("ExposedPorts"),
    }


def product_smoke(
    launcher: Path, image_ref: str, source_revision: str,
    source_tree: str, contract: str,
) -> bool:
    payload = json.dumps({
        "contract_digest": contract, "image_id": image_ref,
        "source_revision": source_revision, "source_tree_digest": source_tree,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME",
            "USERPROFILE", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
        }
    }
    environment.update({
        "TEMPLATE_V2_DEPLOYMENT_TIER": "production",
        "AUTHORED_RENDERER_PRODUCTION_STRICT": "true",
        "AUTHORED_RENDERER_MODE": "isolated",
        "AUTHORED_RENDERER_OS_ISOLATION": "nonroot-readonly-tmpfs-network-none",
        "AUTHORED_RENDERER_KILL_SWITCH": "false",
        "AUTHORED_RENDERER_LEGACY_ENABLED": "false",
        "AUTHORED_RENDERER_IMAGE_REF": image_ref,
        "AUTHORED_RENDERER_SOURCE_REVISION": source_revision,
        "AUTHORED_RENDERER_SOURCE_TREE_DIGEST": source_tree,
        "AUTHORED_RENDERER_CONTRACT_DIGEST": contract,
        "AUTHORED_RENDERER_DEPLOYMENT_ATTESTATION": "sha256:" + hashlib.sha256(payload).hexdigest(),
    })
    completed = subprocess.run(
        [sys.executable, str(launcher)],
        input=(
            b'{"schema_version":"authored-render-request/v1",'
            b'"html":"<main>isolated product smoke</main>","timeout":30}\n'
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0 or completed.stderr:
        return False
    response = json.loads(completed.stdout)
    try:
        png = base64.b64decode(response.get("png_base64", ""), validate=True)
    except (ValueError, TypeError, binascii.Error):
        return False
    return response.get("ok") is True and png.startswith(b"\x89PNG\r\n\x1a\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", type=Path, required=True)
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--source-tree-digest", required=True)
    parser.add_argument("--contract-digest", required=True)
    args = parser.parse_args()
    try:
        inspect = _one(json.loads(args.inspect.read_text(encoding="utf-8-sig")))
        observation = json.loads(args.observation.read_text(encoding="utf-8-sig"))
        if not isinstance(observation, dict):
            raise ValueError("observation must be an object")
        observation["deployment"] = controls(
            inspect, args.source_revision, args.source_tree_digest, args.contract_digest
        )
        observation["deployment"]["product_worker_smoke"] = product_smoke(
            args.launcher, args.image_ref, args.source_revision,
            args.source_tree_digest, args.contract_digest,
        )
        temporary = args.observation.with_name(
            f".{args.observation.name}.tmp-{os.getpid()}"
        )
        temporary.write_text(
            json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, args.observation)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"container inspect verification failed: {type(exc).__name__}")
        return 3
    failed = [key for key, passed in observation["deployment"].items() if not passed]
    print("deployment=" + ("PASS" if not failed else "FAIL:" + ",".join(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
