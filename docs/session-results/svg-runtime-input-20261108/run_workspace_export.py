"""One normal local workspace export; private durable evidence, no retries."""
import argparse
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from http.cookies import SimpleCookie
from http.client import IncompleteRead
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4
from zipfile import ZipFile

PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter"}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def save(path, data):
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def save_json(path, value):
    save(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-result", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:5000")
    parser.add_argument("--auth-file", type=Path)
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    summary = {"schema": "normal-workspace-export-audit/v1", "passed": False}
    stage, sequence, created = "prepare", 0, False
    try:
        directory.mkdir(mode=0o700, exist_ok=False)
        created = True
        publication_bytes = args.publication_result.read_bytes()
        publication = json.loads(publication_bytes)
        save(directory / "publication-result.json", publication_bytes)
        require(publication.get("status") == "published", "publication_not_published")
        presentation_id = str(UUID(publication["presentationId"]))
        revision = publication["revision"]
        require(type(revision) is int and revision > 0, "publication_revision_invalid")
        base = args.base_url.rstrip("/")
        parsed = urlsplit(base)
        require(parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and not parsed.path, "base_url_invalid")
        require(parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback,
                "local_loopback_base_required")
        headers = {"Accept": "application/json"}
        if args.auth_file:
            token = json.loads(args.auth_file.read_text(encoding="utf-8"))["sessionCookie"]
            require(isinstance(token, str) and token and "\r" not in token and "\n" not in token,
                    "existing_session_cookie_required")
            cookie = SimpleCookie()
            cookie["presenton_session"] = token
            headers["Cookie"] = cookie.output(header="", sep=";").strip()
        body = {"command": "Export PPTX", "command_type": "export.pptx",
                "idempotency_key": str(uuid4()), "expected_revision": revision,
                "context": {"quality_policy": "interactive-v1"}}
        command_bytes = (json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        save(directory / "command-request.json", command_bytes)
        summary.update(presentationId=presentation_id, sourceRevision=revision,
                       publicationSha256=hashlib.sha256(publication_bytes).hexdigest(),
                       commandSha256=hashlib.sha256(command_bytes).hexdigest())
        deadline, opener = time.monotonic() + 600, build_opener(ProxyHandler({}), NoRedirect())

        def request(path, *, payload=None, binary=False):
            nonlocal sequence
            sequence += 1
            require(time.monotonic() < deadline, "export_deadline_exhausted")
            request_headers = dict(headers)
            if payload is not None:
                request_headers["Content-Type"] = "application/json"
            req = Request(base + path, data=payload,
                          headers=request_headers, method="GET" if payload is None else "POST")
            save_json(directory / f"http-{sequence:04d}-started.json",
                      {"method": req.method, "path": path, "stage": stage})
            try:
                response = opener.open(req, timeout=max(0.1, min(30, deadline - time.monotonic())))
            except HTTPError as error:
                response = error
            with response:
                save_json(directory / f"http-{sequence:04d}-response.json",
                          {"status": response.status, "headers": dict(response.headers)})
                body_path = directory / f"http-{sequence:04d}-body.bin"
                received, complete = 0, False
                with body_path.open("xb") as output:
                    try:
                        while True:
                            require(time.monotonic() < deadline, "export_deadline_exhausted")
                            chunk = response.read1(64 * 1024)
                            if not chunk:
                                length = response.headers.get("Content-Length")
                                require(length is None or int(length) == received, "http_content_length_mismatch")
                                require(time.monotonic() < deadline, "export_deadline_exhausted")
                                complete = True
                                break
                            output.write(chunk)
                            received += len(chunk)
                    except IncompleteRead as error:
                        output.write(error.partial)
                        received += len(error.partial)
                        raise
                    finally:
                        output.flush()
                        os.fsync(output.fileno())
                        save_json(directory / f"http-{sequence:04d}-transfer.json",
                                  {"complete": complete, "receivedBytes": received})
                require(complete, "http_response_incomplete")
                require(200 <= response.status < 300, f"http_{response.status}")
                data = body_path.read_bytes()
                return (data, response.headers) if binary else json.loads(data)

        stage = "submit"
        print("submitting export", flush=True)
        receipt = request(f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands", payload=command_bytes)
        operation_id = str(UUID(receipt["operation_id"]))
        summary["operationId"] = operation_id
        save_json(directory / "command-receipt.json", receipt)
        stage = "poll"
        print("waiting for export", flush=True)
        while True:
            cursor, seen, item = None, set(), None
            while True:
                query = {"limit": 200}
                if cursor is not None:
                    query["before_position"] = cursor
                timeline = request(f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline?" + urlencode(query))
                found = [row for row in timeline["items"] if row.get("anchor_type") == "operation"
                         and row.get("operation_id") == operation_id]
                require(len(found) <= 1, "operation_ambiguous")
                if found:
                    item = found[0]
                    break
                if not timeline.get("has_more"):
                    break
                cursor = timeline.get("next_before_position")
                require(type(cursor) is int and cursor > 0 and cursor not in seen, "timeline_cursor_invalid")
                seen.add(cursor)
            if item and item["status"] in TERMINAL:
                break
            require(time.monotonic() < deadline, "export_deadline_exhausted")
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        save_json(directory / "terminal-operation.json", item)
        summary["terminalStatus"] = item["status"]
        require(item["status"] == "succeeded", "export_terminal_failure")
        require(item.get("operation_type") == "export.pptx" and item.get("expected_revision") == revision,
                "export_operation_binding_mismatch")
        result = item["result"]
        require(type(result.get("source_revision")) is int and result["source_revision"] == revision,
                "export_source_revision_mismatch")
        require(result.get("artifact_kind") == "export.pptx" and result.get("format") == "pptx", "export_kind_mismatch")
        artifact_id = str(UUID(result["artifact_id"]))
        readback = result.get("public_export_worker_evidence", {}).get("structuralReadback")
        require(isinstance(readback, dict) and readback, "structural_readback_missing")
        save_json(directory / "structural-readback.json", readback)
        stage = "download"
        print("downloading export", flush=True)
        payload, response_headers = request(f"/api/v1/ppt/workspace/artifacts/{artifact_id}/download", binary=True)
        save(directory / "export.pptx", payload)
        actual_sha = hashlib.sha256(payload).hexdigest()
        require(response_headers.get_content_type() == PPTX_TYPE, "pptx_content_type_mismatch")
        require(type(result.get("artifact_size_bytes")) is int and len(payload) == result["artifact_size_bytes"], "pptx_size_mismatch")
        require(isinstance(result.get("artifact_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", result["artifact_sha256"])
                and actual_sha == result["artifact_sha256"], "pptx_digest_mismatch")
        with ZipFile(io.BytesIO(payload)) as package:
            require({"[Content_Types].xml", "ppt/presentation.xml"} <= set(package.namelist()), "pptx_package_invalid")
        summary.update(passed=True, artifactId=artifact_id, artifactSha256=actual_sha, artifactSizeBytes=len(payload))
    except Exception as error:
        summary.update(failedStage=stage, failure=type(error).__name__, detail=str(error))
        if stage == "submit":
            summary["submissionOutcome"] = "unresolved; reconcile saved idempotency key before any retry"
    finally:
        if created:
            save_json(directory / "audit-result.json", summary)
    print("export audit passed" if summary["passed"] else "export audit failed")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    try:
        outcome = main()
    except Exception:
        print("export audit failed while recording private evidence")
        outcome = 1
    raise SystemExit(outcome)
