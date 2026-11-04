#!/usr/bin/env python3
"""Register a PPTX as a published SVG user template through the HTTP API and print its pin.

Dev tool for ``run_eval.py``. It needs a backend that runs the template feature
(``SVG_USER_TEMPLATES_ENABLED=true``) and answers without a login (``DISABLE_AUTH=true`` with a
bootstrap administrator, as in ``scripts/fake_llm_e2e``), or ``--header`` carrying credentials.

Steps: upload the document, snapshot it as a reference, register it as a framed template, wait for
the analysis, substitute every font the server cannot use with the suggested one (the person's
explicit decision in the product; here it is automatic), wait for the re-analysis, publish every frame.

    register_template.py --pptx deck.pptx --name my-template --pin-out pin.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

API = "/api/v1/ppt"


def _check(response: httpx.Response, what: str) -> dict:
    if response.status_code >= 400:
        raise SystemExit(f"{what} failed: HTTP {response.status_code} {response.text[:300]}")
    return response.json()


def _wait(client: httpx.Client, operation_id: str, label: str, timeout: float) -> dict:
    started = time.time()
    while True:
        data = _check(client.get(f"{API}/svg-templates/v1/operations/{operation_id}"), "operation poll")
        if data.get("state") not in {"running", "queued", "pending"}:
            print(f"{label}: {data.get('state')} in {round(time.time() - started)} s", file=sys.stderr)
            if data.get("state") != "succeeded":
                raise SystemExit(f"{label} did not succeed: {json.dumps(data)[:400]}")
            return data
        if time.time() - started > timeout:
            raise SystemExit(f"{label} timed out after {timeout:.0f} s")
        time.sleep(8)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pptx", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--header", action="append", default=[], help="extra request header, K=V (repeatable)")
    parser.add_argument("--pin-out", help="write the pin JSON here as well as to stdout")
    parser.add_argument("--timeout", type=float, default=1200, help="seconds to wait per analysis")
    args = parser.parse_args()
    headers = dict(item.split("=", 1) for item in args.header)
    with httpx.Client(base_url=args.base, headers=headers, timeout=900) as client:
        with open(args.pptx, "rb") as handle:
            asset = _check(client.post(f"{API}/assets/documents", files={"file": (
                "template.pptx",
                handle,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
                headers={"Idempotency-Key": f"eval-doc-{time.time_ns()}"}), "upload")
        snapshot = _check(client.post(f"{API}/references/snapshots", json={
            "provider_id": "uploaded_document", "source": {"asset_ids": [asset["asset_id"]]}},
            headers={"Idempotency-Key": f"eval-snap-{time.time_ns()}"}), "snapshot")
        registered = _check(client.post(f"{API}/svg-templates/v1/register", json={
            "name": args.name, "referenceSnapshotId": snapshot["snapshot_id"],
            "registrationMode": "framed"}), "register")
        template_id = registered["template"]["templateId"]
        first = _wait(client, registered["operationId"], "analysis", args.timeout)
        package = first["result"]["package"]
        operation_id = registered["operationId"]
        unavailable = package.get("unavailable_fonts") or []
        if unavailable:
            chosen: dict[str, dict] = {}
            for item in unavailable:
                chosen.setdefault(item["source_family"], {
                    "sourceFamily": item["source_family"], "targetFamily": item["suggested_family"],
                    "targetWeight": item["suggested_weight"]})
            revised = _check(client.patch(f"{API}/svg-templates/v1/{template_id}/draft", json={
                "operationId": operation_id, "expectedHeadRevision": 0,
                "fontSubstitutions": list(chosen.values())}), "font substitution")
            operation_id = revised["operationId"]
            package = _wait(client, operation_id, "re-analysis", args.timeout)["result"]["package"]
        published = _check(client.post(f"{API}/svg-templates/v1/{template_id}/publish", json={
            "operationId": operation_id, "expectedHeadRevision": 0,
            "selectedFrameIds": [frame["frame_id"] for frame in package["frames"]],
            "confirmReview": True, "styleOnly": False}), "publish")
    pin = published["template"]["pin"]
    text = json.dumps(pin, ensure_ascii=False)
    if args.pin_out:
        with open(args.pin_out, "w", encoding="utf-8") as handle:
            handle.write(text)
    print(text)


if __name__ == "__main__":
    main()
