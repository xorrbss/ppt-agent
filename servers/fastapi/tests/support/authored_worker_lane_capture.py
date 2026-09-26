"""Browser observation and atomic capture for worker-lane integration fixtures."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from services.authored_admission_context import digest_html
from services.authored_local_fit_policy import local_fit_policy
from utils.slide_capture_local_fit import local_fit_render_identity
from utils.slide_capture_result import RenderCapture


class _DomObserver(HTMLParser):
    """Derive the browser telemetry Chromium's probe would report for a page.

    One entry per ``data-qid`` element with its stamped semantic role, its
    directly owned text and its descendant text, every font loaded at a size
    above the floor.  The QualityReceipt v2 capacity gate refuses anything
    that is not a real per-element observation, so a token dict cannot stand
    in for the browser here.
    """

    _VOID = {"meta", "br", "hr", "img", "col", "link", "input"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[dict[str, Any] | None] = []
        self.records: list[dict[str, Any]] = []

    def _open(self, tag: str, attrs) -> dict[str, Any] | None:
        mapped = {key: (value or "") for key, value in attrs}
        qid = mapped.get("data-qid", "").strip()
        if not qid:
            return None
        record = {
            "qid": qid,
            "tag": tag,
            "semantic_role": mapped.get("data-semantic-role", "").strip(),
            "owned": [],
            "rendered": [],
        }
        self.records.append(record)
        return record

    def handle_starttag(self, tag, attrs):
        record = self._open(tag, attrs)
        if tag not in self._VOID:
            self._stack.append(record)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if not self._stack:
            return
        owner = self._stack[-1]
        if owner is not None:
            owner["owned"].append(data)
        for record in self._stack:
            if record is not None:
                record["rendered"].append(data)


def _observe_dom(html: str) -> dict[str, Any]:
    observer = _DomObserver()
    observer.feed(html)
    observer.close()
    elements = []
    for record in observer.records:
        owned = re.sub(r"\s+", " ", "".join(record["owned"])).strip()
        rendered = re.sub(r"\s+", " ", "".join(record["rendered"])).strip()
        elements.append(
            {
                "qid": record["qid"],
                "tag": record["tag"],
                "semantic_role": record["semantic_role"],
                "rendered_text": rendered,
                "owned_text": owned,
                "has_owned_text": bool(owned),
                "font_size": 18,
                "font_loaded": True,
                "css_visible": True,
            }
        )
    return {"elements": elements}


async def capture_for_lane(html: str, png: bytes, **kwargs):
    fit = bool(kwargs.get("fit"))
    policy = kwargs.get("fit_policy") or local_fit_policy(
        author_revision=digest_html(html),
        original_html_digest=digest_html(html),
        slide_index=0,
    )
    metrics = _observe_dom(html)
    metrics["local_fit_mode"] = fit
    metrics["local_fit_render_identity"] = local_fit_render_identity(
        html, policy, fit=fit
    )
    owned = next(
        (element for element in metrics["elements"] if element["has_owned_text"]),
        None,
    )
    qid = owned["qid"] if owned is not None else "fixture-body"
    run = {
        "run_id": f"{qid}-run-0",
        "owner_qid": qid,
        "text_node_ordinal": 0,
        "start_offset": 0,
        "end_offset": len(owned["owned_text"]) if owned is not None else 0,
        "content_digest": digest_html(owned["owned_text"] if owned is not None else ""),
        "group_id": f"{qid}-group",
        "role": "body",
        "ancestor_qids": [qid],
        "original_pt": 18.0,
        "final_pt": 18.0,
    }
    group = {
        "group_id": f"{qid}-group",
        "run_ids": [run["run_id"]],
        "role": "body",
        "card_qid": qid,
        "original_pt": 18.0,
    }
    return RenderCapture.from_payload(
        html_digest=digest_html(html),
        png=png,
        browser_metrics=metrics,
        runtime_counters={},
        fit_report={
            "requested": fit,
            "fit_state": "final",
            "status": "unchanged",
            "policy_revision": policy["policy_revision"],
            "changed": False,
            "partial": False,
            "error": None,
            "operations": [],
            "edits": [],
            "unresolved_reasons": [],
            "baseline": {
                "complete": True,
                "author_revision": policy["author_revision"],
                "original_html_digest": policy["original_html_digest"],
                "runs": [run],
                "groups": [group],
            },
            "runs": [run],
            "groups": [group],
        },
        prepared_html=html,
        prepared_fit_policy=policy,
    )

