"""Live HTTP/auth acceptance for the source-locked v1 queue.

This script targets an already-running, isolated production FastAPI process.
It deliberately uses browser login cookies and never imports the ASGI app.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import secrets
import time
from zipfile import ZipFile

import httpx
import pdfplumber

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.contracts import GenerationMode, PageCountPolicy
from services.source_locked.digests import idempotency_payload_digest, sha256_digest


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def require_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise AssertionError(
            f"{label}: expected HTTP {expected}, got {response.status_code}: "
            f"{response.text[:1000]}"
        )


def source_contract(path: Path) -> tuple[bytes, str]:
    payload = path.read_bytes()
    contract = admit_source_locked_v1(
        [path.name],
        generation_mode=GenerationMode.SOURCE_LOCKED,
        page_count_policy=PageCountPolicy.SOURCE,
        preserve_all_image_occurrences=True,
    )
    return payload, idempotency_payload_digest(contract, sha256_digest(payload))


def create_job(
    client: httpx.Client,
    path: Path,
    *,
    key: str,
    payload_digest: str,
) -> httpx.Response:
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else PPTX_MEDIA_TYPE
    with path.open("rb") as source:
        return client.post(
            "/api/v1/ppt/source-locked/v1/jobs",
            headers={
                "Idempotency-Key": key,
                "X-Idempotency-Payload-Digest": payload_digest,
            },
            data={
                "generation_mode": "source_locked",
                "page_count_policy": "source",
                "preserve_all_image_occurrences": "true",
            },
            files={"files": (path.name, source, media_type)},
        )


def wait_terminal(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> tuple[dict, list[str], float]:
    started = time.monotonic()
    statuses: list[str] = []
    while time.monotonic() - started < timeout_seconds:
        response = client.get(f"/api/v1/ppt/source-locked/v1/jobs/{job_id}")
        require_status(response, 200, "owner job status")
        body = response.json()
        status = body["status"]
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status in {"succeeded", "failed", "cancelled"}:
            return body, statuses, time.monotonic() - started
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not become terminal: {statuses}")


def pptx_slide_count(payload: bytes) -> int:
    with ZipFile(BytesIO(payload)) as package:
        return sum(
            name.startswith("ppt/slides/slide")
            and name.endswith(".xml")
            and name.removeprefix("ppt/slides/slide").removesuffix(".xml").isdigit()
            for name in package.namelist()
        )


def run_success_job(
    owner: httpx.Client,
    path: Path,
    *,
    key: str,
) -> tuple[dict, bytes, dict]:
    source_bytes, digest = source_contract(path)
    started = time.monotonic()
    created = create_job(owner, path, key=key, payload_digest=digest)
    create_seconds = time.monotonic() - started
    require_status(created, 202, f"create {path.suffix} job")
    initial = created.json()
    terminal, statuses, poll_seconds = wait_terminal(owner, initial["job_id"])
    if terminal["status"] != "succeeded":
        raise AssertionError(
            f"{path.name} job failed: {json.dumps(terminal, ensure_ascii=False)}"
        )
    result = terminal["result"]
    artifact = result["artifact"]
    downloaded = owner.get(artifact["download_url"])
    require_status(downloaded, 200, f"download {path.suffix} artifact")
    if len(downloaded.content) != artifact["byte_length"]:
        raise AssertionError("download byte length does not match artifact receipt")
    artifact_hash = "sha256:" + hashlib.sha256(downloaded.content).hexdigest()
    if artifact_hash != artifact["sha256"]:
        raise AssertionError("download digest does not match artifact receipt")
    return terminal, downloaded.content, {
        "create_http": created.status_code,
        "create_seconds": round(create_seconds, 3),
        "poll_seconds": round(poll_seconds, 3),
        "statuses": statuses,
        "artifact_etag": downloaded.headers.get("etag"),
        "artifact_cache_control": downloaded.headers.get("cache-control"),
        "artifact_nosniff": downloaded.headers.get("x-content-type-options"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--admin-username")
    parser.add_argument("--admin-password")
    args = parser.parse_args()

    suffix = secrets.token_hex(4)
    admin_username = args.admin_username or f"owner_admin_{suffix}"
    other_username = f"owner_other_{suffix}"
    admin_password = args.admin_password or secrets.token_urlsafe(18)
    other_password = secrets.token_urlsafe(18)
    client_args = {
        "base_url": args.base_url.rstrip("/"),
        "timeout": httpx.Timeout(180.0, connect=10.0),
    }

    anonymous = httpx.Client(**client_args)
    owner = httpx.Client(**client_args)
    other = httpx.Client(**client_args)
    try:
        status = anonymous.get("/api/v1/auth/status")
        require_status(status, 200, "initial auth status")
        if status.json()["configured"]:
            if not args.admin_username or not args.admin_password:
                raise AssertionError(
                    "configured server requires --admin-username and --admin-password"
                )
            setup_http = None
        else:
            setup = anonymous.post(
                "/api/v1/auth/setup",
                json={"username": admin_username, "password": admin_password},
            )
            require_status(setup, 200, "admin setup")
            setup_http = setup.status_code
        admin_login = owner.post(
            "/api/v1/auth/login",
            json={"username": admin_username, "password": admin_password},
        )
        require_status(admin_login, 200, "admin login")
        owner_id = admin_login.json()["id"]

        created_other = owner.post(
            "/api/v1/admin/users",
            json={"username": other_username, "password": other_password},
        )
        require_status(created_other, 201, "other owner create")
        other_id = created_other.json()["id"]
        other_login = other.post(
            "/api/v1/auth/login",
            json={"username": other_username, "password": other_password},
        )
        require_status(other_login, 200, "other owner login")

        unauthenticated_list = anonymous.get("/api/v1/ppt/source-locked/v1/jobs")
        require_status(unauthenticated_list, 401, "unauthenticated job list")

        pptx_source = args.pptx.read_bytes()
        pptx_source_sha = hashlib.sha256(pptx_source).hexdigest()
        pptx_slides = pptx_slide_count(pptx_source)
        pptx_key = f"source-locked-live-pptx-success-{suffix}"
        pptx_terminal, pptx_download, pptx_timing = run_success_job(
            owner, args.pptx, key=pptx_key
        )
        pptx_job_id = pptx_terminal["job_id"]
        pptx_result = pptx_terminal["result"]
        if pptx_download != pptx_source:
            raise AssertionError("PPTX artifact is not byte-exact to the uploaded fixture")
        if pptx_result["summary"]["slide_count"] != pptx_slides:
            raise AssertionError("PPTX result slide count differs from OOXML source count")
        if pptx_result["restoration_verification"]["missing_content_count"] != 0:
            raise AssertionError("PPTX restoration reported missing content")
        if not pptx_result["restoration_verification"]["byte_exact_source_package"]:
            raise AssertionError("PPTX restoration did not assert byte-exact packaging")

        owner_list_response = owner.get("/api/v1/ppt/source-locked/v1/jobs?limit=200")
        require_status(owner_list_response, 200, "owner job list")
        owner_list = owner_list_response.json()
        if pptx_job_id not in {item["job_id"] for item in owner_list}:
            raise AssertionError("owner job is absent from owner list")
        owner_status_response = owner.get(
            f"/api/v1/ppt/source-locked/v1/jobs/{pptx_job_id}"
        )
        require_status(owner_status_response, 200, "owner job status after success")

        other_list_response = other.get("/api/v1/ppt/source-locked/v1/jobs?limit=200")
        require_status(other_list_response, 200, "other owner job list")
        other_list = other_list_response.json()
        if pptx_job_id in {item["job_id"] for item in other_list}:
            raise AssertionError("another owner can see the PPTX job in list")
        other_status = other.get(f"/api/v1/ppt/source-locked/v1/jobs/{pptx_job_id}")
        require_status(other_status, 404, "other owner job status")
        other_cancel = other.post(
            f"/api/v1/ppt/source-locked/v1/jobs/{pptx_job_id}/cancel"
        )
        require_status(other_cancel, 404, "other owner job cancel")
        artifact_url = pptx_result["artifact"]["download_url"]
        other_artifact = other.get(artifact_url)
        require_status(other_artifact, 404, "other owner artifact")
        anonymous_artifact = anonymous.get(artifact_url)
        require_status(anonymous_artifact, 401, "unauthenticated artifact")

        cancel_source, cancel_digest = source_contract(args.pptx)
        owner_cancel = httpx.Client(
            **client_args,
            cookies=dict(owner.cookies),
        )
        try:
            cancel_response = None
            cancel_create = None
            cancel_job_id = None
            accepted_pre_status = None
            accepted_pre_http = None
            cancel_attempts: list[dict[str, object]] = []
            for attempt_number in range(1, 9):
                cancel_key = (
                    f"source-locked-live-pptx-running-cancel-{suffix}-{attempt_number}"
                )
                created = create_job(
                    owner,
                    args.pptx,
                    key=cancel_key,
                    payload_digest=cancel_digest,
                )
                require_status(created, 202, "create running-cancel job")
                candidate_job_id = created.json()["job_id"]
                attempt_record: dict[str, object] = {
                    "attempt": attempt_number,
                    "job_id": candidate_job_id,
                    "create_http": created.status_code,
                    "observed_statuses": [],
                }
                observation_deadline = time.monotonic() + 3.0
                while time.monotonic() < observation_deadline:
                    before_cancel = owner_cancel.get(
                        f"/api/v1/ppt/source-locked/v1/jobs/{candidate_job_id}"
                    )
                    require_status(before_cancel, 200, "pre-cancel owner status")
                    before_status = before_cancel.json()["status"]
                    observed = attempt_record["observed_statuses"]
                    assert isinstance(observed, list)
                    if not observed or observed[-1] != before_status:
                        observed.append(before_status)
                    if before_status == "running":
                        candidate_cancel = owner_cancel.post(
                            f"/api/v1/ppt/source-locked/v1/jobs/"
                            f"{candidate_job_id}/cancel"
                        )
                        attempt_record["pre_cancel_http"] = before_cancel.status_code
                        attempt_record["pre_cancel_status"] = before_status
                        attempt_record["cancel_http"] = candidate_cancel.status_code
                        attempt_record["cancel_result_status"] = (
                            candidate_cancel.json().get("status")
                            if candidate_cancel.status_code in {200, 202}
                            else None
                        )
                        if candidate_cancel.status_code == 202:
                            cancel_response = candidate_cancel
                            cancel_create = created
                            cancel_job_id = candidate_job_id
                            accepted_pre_status = before_status
                            accepted_pre_http = before_cancel.status_code
                        elif candidate_cancel.status_code != 200:
                            raise AssertionError(
                                "running-state cancel returned unexpected HTTP "
                                f"{candidate_cancel.status_code}: "
                                f"{candidate_cancel.text[:1000]}"
                            )
                        break
                    if before_status in {"succeeded", "failed", "cancelled"}:
                        attempt_record["terminal_before_running_cancel"] = before_status
                        break
                    time.sleep(0.005)
                cancel_attempts.append(attempt_record)
                if cancel_response is not None:
                    break
            if cancel_response is None:
                raise AssertionError(
                    "cancel was never accepted from an observed running state; "
                    f"attempts={cancel_attempts}"
                )
            assert cancel_create is not None
            assert cancel_job_id is not None
            cancelled = cancel_response.json()
            if accepted_pre_http != 200 or accepted_pre_status != "running":
                raise AssertionError("accepted cancel lacks an HTTP 200 running pre-state")
            if cancelled["job_id"] != cancel_job_id:
                raise AssertionError("cancel response job id differs from observed running job")
            if cancelled["status"] != "cancelled" or not cancelled["cancel_requested"]:
                raise AssertionError("accepted cancel did not return cancelled state")
            post_cancel_statuses = []
            for _ in range(30):
                current = owner_cancel.get(
                    f"/api/v1/ppt/source-locked/v1/jobs/{cancel_job_id}"
                )
                require_status(current, 200, "post-cancel owner status")
                current_status = current.json()["status"]
                post_cancel_statuses.append(current_status)
                if current_status == "succeeded":
                    raise AssertionError("accepted cancel later transitioned to succeeded")
                time.sleep(0.1)
            if set(post_cancel_statuses) != {"cancelled"}:
                raise AssertionError(
                    f"accepted cancel was not terminally stable: {post_cancel_statuses}"
                )
        finally:
            owner_cancel.close()

        report: dict[str, object] = {
            "auth": {
                "admin_setup_http": setup_http,
                "admin_login_http": admin_login.status_code,
                "owner_id": owner_id,
                "other_owner_id": other_id,
                "other_login_http": other_login.status_code,
                "unauthenticated_list_http": unauthenticated_list.status_code,
            },
            "pptx": {
                "path": str(args.pptx.resolve()),
                "bytes": len(pptx_source),
                "sha256": pptx_source_sha,
                "source_slides": pptx_slides,
                "job_id": pptx_job_id,
                "status": pptx_terminal["status"],
                "result_slides": pptx_result["summary"]["slide_count"],
                "missing_content_count": pptx_result["restoration_verification"][
                    "missing_content_count"
                ],
                "artifact_id": pptx_result["artifact"]["artifact_id"],
                "artifact_bytes": len(pptx_download),
                "artifact_sha256": hashlib.sha256(pptx_download).hexdigest(),
                "byte_exact": pptx_download == pptx_source,
                **pptx_timing,
            },
            "owner_isolation": {
                "owner_list_http": owner_list_response.status_code,
                "owner_list_count": len(owner_list),
                "owner_status_http": owner_status_response.status_code,
                "other_list_http": other_list_response.status_code,
                "other_list_count": len(other_list),
                "other_job_visible": pptx_job_id
                in {item["job_id"] for item in other_list},
                "other_status_http": other_status.status_code,
                "other_cancel_http": other_cancel.status_code,
                "other_artifact_http": other_artifact.status_code,
                "anonymous_artifact_http": anonymous_artifact.status_code,
            },
            "cancel_race": {
                "job_id": cancel_job_id,
                "create_http": cancel_create.status_code,
                "accepted_http": cancel_response.status_code,
                "accepted_pre_http": accepted_pre_http,
                "accepted_pre_status": accepted_pre_status,
                "attempt_count": len(cancel_attempts),
                "attempts": cancel_attempts,
                "accepted_status": cancelled["status"],
                "cancel_requested": cancelled["cancel_requested"],
                "post_cancel_observations": len(post_cancel_statuses),
                "post_cancel_statuses": sorted(set(post_cancel_statuses)),
                "succeeded_after_accept": "succeeded" in post_cancel_statuses,
                "uploaded_bytes": len(cancel_source),
            },
        }

        if args.pdf is not None:
            pdf_source = args.pdf.read_bytes()
            pdf_sha = hashlib.sha256(pdf_source).hexdigest()
            with pdfplumber.open(BytesIO(pdf_source)) as source_pdf:
                pdf_pages = len(source_pdf.pages)
            pdf_key = f"source-locked-live-pdf-success-{suffix}"
            pdf_terminal, pdf_download, pdf_timing = run_success_job(
                owner, args.pdf, key=pdf_key
            )
            pdf_result = pdf_terminal["result"]
            pdf_summary = pdf_result["summary"]
            pdf_verification = pdf_result["restoration_verification"]
            artifact_slides = pptx_slide_count(pdf_download)
            if pdf_summary["page_count"] != pdf_pages:
                raise AssertionError("PDF result page count differs from source")
            if pdf_summary["slide_count"] != pdf_pages:
                raise AssertionError("PDF result slide count differs from source page count")
            if pdf_verification["slide_count"] != pdf_pages:
                raise AssertionError("PDF restoration slide count differs from source")
            if artifact_slides != pdf_pages:
                raise AssertionError("PDF artifact OOXML slide count differs from source")
            if pdf_verification["missing_content_count"] != 0:
                raise AssertionError("PDF restoration reported missing content")
            report["pdf"] = {
                "path": str(args.pdf.resolve()),
                "bytes": len(pdf_source),
                "sha256": pdf_sha,
                "source_pages": pdf_pages,
                "job_id": pdf_terminal["job_id"],
                "status": pdf_terminal["status"],
                "result_pages": pdf_summary["page_count"],
                "result_slides": pdf_summary["slide_count"],
                "artifact_ooxml_slides": artifact_slides,
                "missing_content_count": pdf_verification["missing_content_count"],
                "artifact_bytes": len(pdf_download),
                "artifact_sha256": hashlib.sha256(pdf_download).hexdigest(),
                **pdf_timing,
            }

        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        anonymous.close()
        owner.close()
        other.close()


if __name__ == "__main__":
    main()
