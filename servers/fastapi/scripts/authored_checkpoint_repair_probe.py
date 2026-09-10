"""Read-only authored checkpoint scan and repair probe.

Adapted from ``artifacts/authored-four-slide-validation-20260910/validate.py``,
created by Codex rollout 01a088d3-ab14-7172-9ea9-7bf764afab18 on 2026-09-10.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(FASTAPI_ROOT))

# Replay the recorded concurrency without changing the protected stack store.
os.environ.setdefault("AUTHORED_RENDER_CONCURRENCY", "2")
os.environ.setdefault("AUTHORED_SLIDE_RENDER_CONCURRENCY", "2")
os.environ.setdefault("AUTHORED_BATCH_WAVE_CONCURRENCY", "5")
os.environ.setdefault("AUTHORED_CLUSTER_JOB_CONCURRENCY", "2")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Scan or exercise authored repairs without mutating the original job."
    )
    parser.add_argument("--job", required=True, type=uuid.UUID, help="Generation job UUID")
    parser.add_argument("--slides", required=True, help="One-based slides, e.g. 1,9,12")
    parser.add_argument("--scan-only", action="store_true", help="Inspect without repair")
    parser.add_argument(
        "--out", type=Path,
        default=REPO_ROOT / "artifacts" / "authored-checkpoint-repair-probe",
        help="Evidence output directory",
    )
    parser.add_argument("--max-cycles", type=int, default=2, help="Repair cycle limit")
    return parser


# Keep --help usable from the repository root even when uv has not selected the
# FastAPI project environment yet.
if __name__ == "__main__" and any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    build_parser().parse_args()


from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from models.authored_header_contract import AuthoredHeaderContract
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.authored_checkpoint_artifacts import read_valid_evidence_artifact
from services.authored_composition_policy import (
    build_authored_capacity_plan, capacity_violations, ensure_authored_semantic_roles,
)
from services.authored_presentation_generation import (
    _capacity_gate_initial_critiques, _deterministic_repair_candidate,
    _require_source_verbatim_bindings, _revise_gate_repair_candidates,
    _source_block_bindings, blocking_safety_issues,
)
from services.authored_source_visibility_repair import source_visibility_issues
from services.authored_structural_repair import (
    make_source_repair_evaluator, validate_repair_result,
)
from services.provider_usage_instrumentation import provider_operation_usage_scope
from services.source_content.browser_readback import inspect_browser_source_readback
from services.source_content.pipeline import (
    admit_source_content_request, prepare_source_content_authored_reflow,
)
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.authored_styles import AuthoredStyle
from utils.generation_model import generation_model_context
from utils.llm_calls.author_slide import Brand, build_design_system
from utils.llm_calls.author_slide_patch import (
    build_structural_repair_scopes, protected_qids_for_repair_scopes,
)
from utils.llm_calls.author_vision_qa import (
    AuthoredVqaResidualDomIssuesError, _measured_structural_issue_qids,
    _structural_compact_dom,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id
from utils.runtime_settings import RuntimeSettings, runtime_settings_context
from utils.slide_capture import get_render_runtime_counters, render_html_to_png_with_metrics
from utils.slide_capture_deck_scope import set_render_deck_scope


def parse_args():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.slide_numbers = [int(x.strip()) for x in args.slides.split(",") if x.strip()]
    except ValueError as error:
        parser.error(f"--slides must be comma-separated integers: {error}")
    if not args.slide_numbers or min(args.slide_numbers) < 1:
        parser.error("--slides must contain positive slide numbers")
    if len(args.slide_numbers) != len(set(args.slide_numbers)):
        parser.error("--slides must not contain duplicates")
    if args.max_cycles < 1:
        parser.error("--max-cycles must be at least 1")
    args.indices = [slide - 1 for slide in args.slide_numbers]
    args.out = args.out.resolve()
    return args


def require_database_url():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must start with postgresql; refusing SQLite")
    return url


def checkpoint_digest(rows):
    payload = [
        {
            "id": str(row.id), "slide": row.slide_index,
            "author": row.author_status, "illustrate": row.illustrate_status,
            "render": row.render_status, "persist": row.persist_status,
            "authorHtml": row.author_html, "illustratedHtml": row.illustrated_html,
            "renderPath": row.render_path, "renderSha256": row.render_sha256,
            "artifactRefs": row.artifact_refs, "failedStage": row.failed_stage,
            "error": row.error, "attemptToken": row.attempt_token,
            "updatedAt": str(row.updated_at),
        }
        for row in sorted(rows, key=lambda item: item.slide_index)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def save_json(out, name, value):
    (out / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


async def load_checkpoints(session, job_id, indices):
    return list((await session.execute(
        select(AuthoredSlideCheckpoint).where(
            AuthoredSlideCheckpoint.job_id == job_id,
            AuthoredSlideCheckpoint.slide_index.in_(indices),
        )
    )).scalars().all())


async def verify_original(engine, job_id, indices, request_sha, attempt, digest_before):
    async with AsyncSession(engine) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        current = (await session.execute(text(
            "SELECT request_sha256, attempt_number FROM presentation_generation_jobs WHERE id=:id"
        ), {"id": job_id})).mappings().one()
        digest_after = checkpoint_digest(await load_checkpoints(session, job_id, indices))
    unchanged = (
        digest_after == digest_before
        and current["request_sha256"] == request_sha
        and current["attempt_number"] == attempt
    )
    return digest_after, unchanged


def runtime_settings():
    path = os.environ.get("USER_CONFIG_PATH")
    config = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    return RuntimeSettings.from_sources(user_config=config)


async def run(args):
    started = time.monotonic()
    args.out.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(require_database_url())
    owner_token = None
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            job = (await session.execute(
                text("SELECT * FROM presentation_generation_jobs WHERE id=:id"),
                {"id": args.job},
            )).mappings().one()
            owner = (await session.execute(
                text("SELECT owner_id FROM workspace_operations WHERE id=:id"),
                {"id": job["workspace_operation_id"]},
            )).scalar_one()
            owner_token = set_current_owner_id(owner)
            payload = job["request_payload"]
            request = GeneratePresentationRequest.model_validate(payload)
            await admit_source_content_request(request, sql_session=session, owner_id=owner)
            prepare_source_content_authored_reflow(request)
            plans = {row.stage: row.payload for row in (await session.execute(
                text("SELECT stage,payload FROM authored_deck_plan_checkpoints "
                     "WHERE generation_job_id=:id"), {"id": args.job}
            )).all()}
            roles = plans["deck_plan"]["roles"]
            contents = [slide["content"] for slide in job["authored_outline"]["slides"]]
            if max(args.indices) >= len(contents):
                raise ValueError(
                    f"--slides exceeds the job's {len(contents)} slides: {args.slide_numbers}"
                )
            profile = request._authored_composition_profile
            capacity = build_authored_capacity_plan(contents, roles, profile)
            header = AuthoredHeaderContract.model_validate(plans["header"]["headerContract"])
            style = AuthoredStyle(**payload["_internal_authored_style_snapshot"])
            brand = Brand(topic="source checkpoint validation")
            design = build_design_system(brand, style, header_contract=header)
            bindings = _source_block_bindings(request)
            manifest = request._source_content_manifest.model_dump(mode="json", by_alias=True)
            outline = request._source_document_outline.model_dump(mode="json", by_alias=True)
            checkpoints = await load_checkpoints(session, args.job, args.indices)
            found = {row.slide_index for row in checkpoints}
            if found != set(args.indices):
                raise RuntimeError(
                    f"checkpoint evidence incomplete; requested={args.slide_numbers}, "
                    f"found={sorted(index + 1 for index in found)}"
                )
            digest_before = checkpoint_digest(checkpoints)
            bundles = {}
            for checkpoint in checkpoints:
                bundle = await read_valid_evidence_artifact(checkpoint)
                if bundle is None:
                    raise RuntimeError(f"checkpoint evidence unavailable:{checkpoint.slide_index + 1}")
                bundles[checkpoint.slide_index] = bundle
            metadata = {
                "originalJob": str(args.job),
                "originalOperation": str(job["workspace_operation_id"]),
                "originalJobAttempt": job["attempt_number"],
                "requestSha256": job["request_sha256"],
                "preset": payload["codex_generation_preset"],
                "totalSlides": len(contents),
                "sourceBlocks": sum(len(slide["readingOrder"]) for slide in outline["slides"]),
                "allManifestBlocks": sum(len(unit["blocks"]) for unit in manifest["units"]),
                "manifestDigest": manifest.get("manifestDigest"),
                "databaseDriver": engine.dialect.driver,
                "selectedSlides": args.slide_numbers,
                "checkpointDigestBefore": digest_before,
                "environment": {key: value for key, value in os.environ.items()
                                if key.startswith("AUTHORED_") and any(marker in key for marker in (
                                    "CONCURRENCY", "BATCH_SIZE", "CLUSTER", "STREAM"))},
            }
            save_json(args.out, "inputs.json", metadata)
            print("INPUTS", json.dumps(metadata, ensure_ascii=False), flush=True)
            htmls, pngs = [""] * len(contents), [b""] * len(contents)
            metrics_list, failures, evaluators, deterministic, results = [], [], {}, {}, []
            capture_ordinals = {index: 0 for index in args.indices}

            async def capture(html, index):
                canonical = ensure_authored_semantic_roles(ensure_authored_qids(html))
                rendered = await render_html_to_png_with_metrics(canonical)
                if rendered.browser_metrics is None:
                    raise RuntimeError(f"capture missing metrics:{index + 1}")
                capture_ordinals[index] += 1
                prefix = f"slide-{index + 1}-attempt-{capture_ordinals[index]}"
                (args.out / f"{prefix}.html").write_text(canonical, encoding="utf-8")
                (args.out / f"{prefix}.png").write_bytes(rendered.png)
                save_json(args.out, f"{prefix}-metrics.json", dict(rendered.browser_metrics))
                return canonical, rendered.png, dict(rendered.browser_metrics)

            with set_render_deck_scope(f"checkpoint-repair-probe-{args.job}"):
                for index in args.indices:
                    original = bundles[index]
                    htmls[index] = original.html
                    (args.out / f"slide-{index + 1}-baseline.html").write_text(
                        original.html, encoding="utf-8"
                    )
                    rendered = await render_html_to_png_with_metrics(original.html)
                    metrics = dict(rendered.browser_metrics or {})
                    if not metrics:
                        raise RuntimeError(f"baseline capture missing metrics:{index + 1}")
                    pngs[index] = rendered.png
                    metrics_list.append(metrics)
                    (args.out / f"slide-{index + 1}-baseline.png").write_bytes(rendered.png)
                    save_json(args.out, f"slide-{index + 1}-baseline-metrics.json", metrics)
                    dom_report = inspect_authored_dom(
                        original.html, metrics, strict_structural_off_canvas=True
                    )
                    source_slide = outline["slides"][index]
                    unit = next(unit for unit in manifest["units"]
                                if unit["sourceUnitId"] == source_slide["sourceUnitId"])
                    source_manifest, source_outline = {"units": [unit]}, {"slides": [source_slide]}
                    readback = inspect_browser_source_readback(
                        manifest=source_manifest, outline=source_outline,
                        htmls=[original.html], browser_metrics=[metrics],
                    )
                    issues = tuple(blocking_safety_issues(dom_report)) + source_visibility_issues(
                        readback.as_dict(), metrics
                    )
                    failures.append({"slide_index": index,
                                     "issues": [issue.to_dict() for issue in issues]})
                    issue_qids = tuple(qid for issue in issues for qid in issue.qids)
                    measured_dom = _structural_compact_dom(original.html, metrics)
                    scopes = build_structural_repair_scopes(
                        measured_dom,
                        issue_qids=tuple(dict.fromkeys((
                            *issue_qids,
                            *_measured_structural_issue_qids(metrics, issue_qids=issue_qids),
                        ))),
                        html=original.html,
                    )

                    def composition(html, measured, *, absolute=index):
                        _require_source_verbatim_bindings([html], (bindings[absolute],))
                        errors = capacity_violations(html, measured, capacity.at(absolute))
                        dom = inspect_authored_dom(
                            html, measured,
                            strict_structural_off_canvas=profile.profile_id != "ordinary",
                        )
                        defects = blocking_safety_issues(dom)
                        contract = [code for code in errors if any(marker in code for marker in (
                            "missing", "unverified", "mismatch", "duplicate", "invalid",
                        )) or code.startswith((
                            "font:", "supporting_font:", "images:", "browser_images:",
                        ))]
                        return {
                            "passed": not errors and not defects
                                      and dom.telemetry_status == "available",
                            "violations": [*errors, *(item.to_dict() for item in defects)],
                            "contract_violations": contract,
                        }

                    evaluators[index] = make_source_repair_evaluator(
                        manifest=source_manifest, outline=source_outline,
                        baseline_html=original.html, baseline_metrics=metrics,
                        composition_validator=composition,
                        context={
                            "slide_index": index, "profile_id": profile.profile_id,
                            "reference_mode": style.reference_mode,
                            "protected_qids": protected_qids_for_repair_scopes(
                                measured_dom, scopes
                            ),
                        },
                    )
                    deterministic[index] = _deterministic_repair_candidate(
                        html=original.html, browser_metrics=metrics,
                        capacity=capacity.at(index), header_contract=header,
                        role=roles[index], profile_id=profile.profile_id,
                        allow_immutable_checkpoint_geometry=True,
                        repair_intrinsic_grid_overlap=True,
                    )
                    save_json(args.out, f"slide-{index + 1}-baseline-report.json", {
                        "issues": [issue.to_dict() for issue in issues],
                        "source": readback.as_dict(),
                        "scopes": [scope.model_dump(mode="json") for scope in scopes],
                        "capacity": list(capacity_violations(
                            original.html, metrics, capacity.at(index)
                        )),
                    })
                    print("BASELINE", index + 1, "issues", len(issues),
                          "sourcePassed", readback.passed, flush=True)

                if args.scan_only:
                    digest_after, unchanged = await verify_original(
                        engine, args.job, args.indices, job["request_sha256"],
                        job["attempt_number"], digest_before,
                    )
                    scan = {
                        **metadata, "checkpointDigestAfter": digest_after,
                        "originalJobMutated": not unchanged,
                        "failedSlides": [{"slide": item["slide_index"] + 1,
                                          "issues": item["issues"]}
                                         for item in failures if item["issues"]],
                        "elapsedSeconds": round(time.monotonic() - started, 2),
                        "renderCounters": get_render_runtime_counters(),
                    }
                    save_json(args.out, "scan-result.json", scan)
                    print("SCAN", json.dumps({
                        "selectedSlides": scan["selectedSlides"],
                        "failedSlides": [item["slide"] for item in scan["failedSlides"]],
                        "checkpointDigestBefore": digest_before,
                        "checkpointDigestAfter": digest_after,
                        "originalJobMutated": not unchanged,
                    }, ensure_ascii=False), flush=True)
                    return scan

                repair_kwargs = dict(
                    slide_indices=args.indices, total_slides=len(contents),
                    max_cycles=args.max_cycles, style=style, return_details=True,
                    local_patch_only=True, fail_on_residual_dom_issues=True,
                    allow_patch_proposal_retry=False,
                    candidate_evaluators=evaluators,
                    structural_results_sink=results,
                    structural_candidate_capture=capture,
                    initial_browser_metrics=metrics_list,
                    initial_critiques=_capacity_gate_initial_critiques(
                        failures, args.indices, roles, allow_text_reduction=False
                    ),
                    provider_recovery_allowed=[True] * len(args.indices),
                )
                with provider_operation_usage_scope() as provider_usage:
                    with generation_model_context(payload["codex_generation_preset"]):
                        try:
                            await _revise_gate_repair_candidates(
                                flagged=args.indices, htmls=htmls, pngs=pngs,
                                contents=contents, roles=roles,
                                deterministic_candidates=deterministic,
                                brand=brand, design_system=design,
                                repair_kwargs=repair_kwargs,
                            )
                        except AuthoredVqaResidualDomIssuesError as error:
                            results = error.repair_results
                            print("RESIDUAL", str(error), flush=True)
                    provider_usage_snapshot = provider_usage.snapshot()
                summary = []
                for result in results:
                    if result.final_status == "admitted":
                        validate_repair_result(result, expected_slide_index=result.slide_index)
                    name = f"slide-{result.slide_index + 1}-candidate"
                    (args.out / f"{name}.html").write_text(result.html, encoding="utf-8")
                    (args.out / f"{name}.png").write_bytes(result.png)
                    save_json(args.out, f"{name}.json", {
                        "assessment": asdict(result.assessment),
                        "history": result.cycle_history,
                    })
                    summary.append({
                        "slide": result.slide_index + 1, "status": result.final_status,
                        "changed": result.changed,
                        "reasons": list(result.assessment.reasons),
                        "cycles": result.cycle_history,
                    })
                digest_after, unchanged = await verify_original(
                    engine, args.job, args.indices, job["request_sha256"],
                    job["attempt_number"], digest_before,
                )
                report = {
                    **metadata, "checkpointDigestAfter": digest_after,
                    "originalJobMutated": not unchanged,
                    "allFourPassed": unchanged and len(summary) == len(args.indices)
                                     and all(x["status"] == "admitted" for x in summary),
                    "slides": summary, "providerUsage": provider_usage_snapshot,
                    "elapsedSeconds": round(time.monotonic() - started, 2),
                    "renderCounters": get_render_runtime_counters(),
                }
                save_json(args.out, "result.json", report)
                print("RESULT", json.dumps(report, ensure_ascii=False), flush=True)
                return report
    finally:
        if owner_token is not None:
            reset_current_owner_id(owner_token)
        await engine.dispose()


def main():
    args = parse_args()
    try:
        with runtime_settings_context(runtime_settings()):
            asyncio.run(run(args))
    except Exception as error:
        database_url = os.environ.get("DATABASE_URL", "")
        message = str(error).replace(database_url, "[REDACTED_DATABASE_URL]")
        print(f"ERROR {type(error).__name__}: {message}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
