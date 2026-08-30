"""Run controlled load with ephemeral signed principals in an isolated RC DB.

JWTs and generated passwords exist only in process memory and an anonymous
stdin pipe. They are never written to the manifest, evidence, argv, environment,
or logs. The FastAPI stack must run with authentication enabled and must point at
the same isolated database and user-config path as this process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid

from sqlalchemy import select


REPO = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO.parent
FASTAPI_ROOT = REPO / "servers" / "fastapi"
sys.path.insert(0, str(FASTAPI_ROOT))

from api.v1.auth.users import PASSWORD_HELPER, get_jwt_strategy  # noqa: E402
from models.sql.public_admission import (  # noqa: E402
    PublicOperationAdmission,
    PublicProviderLogicalCall,
)
from models.sql.user import User  # noqa: E402
from services.database import async_session_maker  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--principal-count", type=int, default=10)
    parser.add_argument("--node", default="node")
    parser.add_argument("--trusted-validation-root", required=True)
    return parser.parse_args()


def _contains_token(path: Path, tokens: list[bytes]) -> bool:
    overlap = max(len(token) for token in tokens) - 1
    tail = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                observed = tail + chunk
                if any(token in observed for token in tokens):
                    return True
                tail = observed[-overlap:] if overlap > 0 else b""
    except (OSError, PermissionError):
        raise RuntimeError("session token leak scan could not read isolated artifact")
    return False


async def _principals(count: int) -> list[str]:
    if count < 1 or count > 20:
        raise ValueError("principal-count must be 1..20")
    strategy = get_jwt_strategy()
    tokens: list[str] = []
    async with async_session_maker() as session:
        for ordinal in range(1, count + 1):
            username = f"release-validation-load-{ordinal:02d}"
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"presenton:{username}"),
                    username=username,
                    hashed_password=PASSWORD_HELPER.hash(secrets.token_urlsafe(32)),
                    is_active=True,
                    is_superuser=False,
                    is_verified=True,
                )
                session.add(user)
                await session.flush()
            elif not user.is_active or user.is_superuser:
                raise RuntimeError("release validation fixture user state is invalid")
            tokens.append(await strategy.write_token(user))
        await session.commit()
    return tokens


async def _database_audit(operation_ids: list[uuid.UUID]) -> dict[str, object]:
    identities: list[tuple[str, str, int | None, int]] = []
    observed_operations: set[str] = set()
    async with async_session_maker() as session:
        admissions = list(
            (
                await session.execute(
                    select(PublicOperationAdmission).where(
                        PublicOperationAdmission.operation_id.in_(operation_ids)
                    )
                )
            ).scalars()
        )
        admission_by_id = {row.id: row for row in admissions}
        if admission_by_id:
            calls = list(
                (
                    await session.execute(
                        select(PublicProviderLogicalCall).where(
                            PublicProviderLogicalCall.admission_id.in_(admission_by_id)
                        )
                    )
                ).scalars()
            )
            for call in calls:
                operation = admission_by_id[call.admission_id].operation_id
                observed_operations.add(str(operation))
                identities.append(
                    (
                        str(operation),
                        call.stage,
                        call.slide_index,
                        call.logical_call_ordinal,
                    )
                )
    return {
        "schemaVersion": "public-service-load-database-audit/v1",
        "operationCount": len(operation_ids),
        "operationsWithProviderCalls": len(observed_operations),
        "providerLogicalCallCount": len(identities),
        "duplicateProviderLogicalCallCount": len(identities) - len(set(identities)),
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _operation_ids(report: dict[str, object], report_path: Path) -> list[uuid.UUID]:
    results = report.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"controlled report has no result array: {report_path}")
    try:
        return [uuid.UUID(str(result["operationId"])) for result in results]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(
            f"controlled report has an invalid operation id: {report_path}"
        ) from error


def _matrix_report_paths(
    output_directory: Path, matrix_report: dict[str, object]
) -> list[Path]:
    scenarios = matrix_report.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise RuntimeError("controlled matrix report has no scenarios")
    report_paths: list[Path] = []
    seen: set[Path] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise RuntimeError("controlled matrix scenario is invalid")
        raw_directory = scenario.get("outputDirectory")
        if not isinstance(raw_directory, str) or not raw_directory:
            raise RuntimeError("controlled matrix scenario has no outputDirectory")
        scenario_directory = Path(raw_directory)
        if not scenario_directory.is_absolute():
            scenario_directory = REPO / scenario_directory
        scenario_directory = scenario_directory.resolve()
        try:
            scenario_directory.relative_to(output_directory)
        except ValueError as error:
            raise RuntimeError(
                "controlled matrix scenario output escaped the manifest outputDirectory"
            ) from error
        report_path = scenario_directory / "controlled-load-report.json"
        if report_path in seen or not report_path.is_file():
            raise RuntimeError(
                f"controlled matrix scenario report is missing or duplicated: {report_path}"
            )
        seen.add(report_path)
        report_paths.append(report_path)
    return report_paths


async def _audit_completed_reports(
    *, output_directory: Path, schema_version: object
) -> tuple[int, int]:
    if schema_version == 2:
        matrix_path = output_directory / "controlled-load-matrix-report.json"
        matrix_report = json.loads(matrix_path.read_text(encoding="utf-8"))
        if (
            matrix_report.get("schemaVersion") != 2
            or matrix_report.get("kind") != "authored-vnext-controlled-load-matrix"
        ):
            raise RuntimeError("controlled matrix report contract is invalid")
        report_paths = _matrix_report_paths(output_directory, matrix_report)
    else:
        matrix_path = None
        matrix_report = None
        report_paths = [output_directory / "controlled-load-report.json"]

    all_operation_ids: list[uuid.UUID] = []
    for report_path in report_paths:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("kind") != "authored-vnext-controlled-load":
            raise RuntimeError(f"controlled report contract is invalid: {report_path}")
        operation_ids = _operation_ids(report, report_path)
        report["databaseAudit"] = await _database_audit(operation_ids)
        _atomic_write_json(report_path, report)
        all_operation_ids.extend(operation_ids)

    if matrix_path is not None and matrix_report is not None:
        matrix_report["databaseAudit"] = await _database_audit(all_operation_ids)
        matrix_report["scenarioReportCount"] = len(report_paths)
        _atomic_write_json(matrix_path, matrix_report)
    return len(report_paths), len(all_operation_ids)


async def _main() -> int:
    args = _args()
    manifest = Path(args.manifest).resolve()
    relative = os.path.relpath(manifest, REPO)
    if relative == ".." or relative.startswith(f"..{os.sep}") or not manifest.is_file():
        raise ValueError("manifest must be a file inside the RC worktree")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    output_directory = (REPO / str(manifest_payload.get("outputDirectory") or "")).resolve()
    output_relative = os.path.relpath(output_directory, REPO)
    if output_relative == ".." or output_relative.startswith(f"..{os.sep}"):
        raise ValueError("outputDirectory must stay inside the RC worktree")
    tokens = await _principals(args.principal_count)
    environment = os.environ.copy()
    environment["PRESENTON_BENCH_PRINCIPALS_STDIN"] = "1"
    environment.pop("PRESENTON_BENCH_USER", None)
    environment.pop("PRESENTON_BENCH_PASSWORD", None)
    completed = subprocess.run(
        [
            args.node,
            str(REPO / "scripts" / "authored-vnext-controlled-load.mjs"),
            "--run",
            "--manifest",
            str(manifest),
        ],
        cwd=REPO,
        env=environment,
        input=json.dumps(tokens, separators=(",", ":")),
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        report_count, operation_count = await _audit_completed_reports(
            output_directory=output_directory,
            schema_version=manifest_payload.get("schemaVersion"),
        )
        token_bytes = [token.encode("utf-8") for token in tokens]
        raw_app_data_directory = os.environ.get("APP_DATA_DIRECTORY")
        if not raw_app_data_directory:
            raise RuntimeError("APP_DATA_DIRECTORY is required for token leak scan")
        app_data_directory = Path(raw_app_data_directory).resolve()
        trusted_validation_root = Path(args.trusted_validation_root).resolve()
        trusted_relative = os.path.relpath(trusted_validation_root, WORKSPACE_ROOT)
        if trusted_relative == ".." or trusted_relative.startswith(f"..{os.sep}"):
            raise RuntimeError("trusted validation root must stay below the PPT-agent workspace")
        app_data_relative = os.path.relpath(app_data_directory, trusted_validation_root)
        if (
            app_data_relative in {"", "."}
            or app_data_relative == ".."
            or app_data_relative.startswith(f"..{os.sep}")
            or "app_data" not in {part.lower() for part in app_data_directory.parts}
        ):
            raise RuntimeError("APP_DATA_DIRECTORY must be an isolated trusted validation app-data path")
        for scan_root in {output_directory, app_data_directory}:
            for candidate in scan_root.rglob("*"):
                if candidate.is_file() and _contains_token(candidate, token_bytes):
                    raise RuntimeError("session token persisted in load evidence")
        receipt = {
            "schemaVersion": "public-service-load-auth-fixture/v1",
            "principalCount": len(tokens),
            "transport": "signed-session-cookie",
            "tokenInArgv": False,
            "tokenInEnvironment": False,
            "tokenInEvidence": False,
            "tokenForwardedToWorkerEnvironment": False,
            "isolatedAppDataLeakScan": "passed",
            "auditedReportCount": report_count,
            "auditedOperationCount": operation_count,
        }
        output_directory.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(output_directory / "auth-fixture-receipt.json", receipt)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
