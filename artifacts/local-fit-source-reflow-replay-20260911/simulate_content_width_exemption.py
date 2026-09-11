"""(c) simulation -- how many failing slides resolve if the width verdict exempts
content-sized text boxes?

``_scopeHard`` rejects a candidate as ``fixed_horizontal`` when a preserved node's
``left`` **or** ``width`` moves past tolerance. For a text box whose width is a
function of its type size -- an inline ``<span white-space:nowrap>``, a bare
``<strong>``, a width-less flex item -- shrinking the font necessarily narrows the
box, so a candidate whose intent was purely vertical is rejected whole. The
rejection breakdown over the failing slides put this rule at 42.7% (148/347), the
single largest blocker.

This measures what relaxing it would buy, **without changing any code**: the
geometry fragment is patched in this process only, and three arms are compared.

    baseline   production ``authored-local-fit/v1.5``, unpatched
    inline     width change forgiven on inline-family text leaves
    shrink     also on shrink-to-fit flex/grid items (a wider reading)
    box        any box measured to be as wide as its own content, container
               or leaf -- the widest reading of (c)
    measured   the same measurement restricted to a text leaf
               and narrower than the space its parent offers

``left`` stays preserved in every arm; only the width half of the verdict is
relaxed, and only for a leaf that carries text and no block-level child.

Read-only: stored ``slides.html_content`` in, Chromium render, JSON out. No
generation, no provider call, no database write, no stack contact.

    python simulate_content_width_exemption.py <validation-dir> <out.json>
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import psycopg  # noqa: E402

from services.authored_composition_policy import blocking_safety_issues  # noqa: E402
from services.authored_local_fit_policy import html_digest, local_fit_policy  # noqa: E402
from utils import slide_capture_fit_script as fit_script  # noqa: E402
from utils.authored_dom_quality import inspect_authored_dom  # noqa: E402
from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402
from utils.slide_capture_fit_geometry import GEOMETRY_MEMBERS  # noqa: E402

#: Kept outside the repository; point ``LOCAL_FIT_DB_URL_FILE`` at it.
DB_URL = Path(os.environ.get("LOCAL_FIT_DB_URL_FILE")
              or Path(__file__).resolve().parent / "dburl.txt")

#: The slides the paired replay left unresolved (``fit-replay-*.json``).
TARGETS: dict[str, list[int]] = {
    "run-11": [16, 26, 29],
    "run-15": [21, 24],
    "run-16": [9, 19, 25, 26],
}

#: Verbatim from ``slide_capture_fit_geometry.py``; split only to stay inside the
#: line limit. ``geometry_for`` refuses to run if it no longer matches.
_WIDTH_VERDICT = (
    "      const horizontal = !labelMorph && (Math.abs(after.left - before.left)"
    " > this.C.geometry_tolerance_px ||" "\n"
    "        Math.abs(after.width - before.width) > this.C.geometry_tolerance_px);"
)

_WIDTH_VERDICT_SIM = (
    "      const horizontal = !labelMorph && (Math.abs(after.left - before.left)"
    " > this.C.geometry_tolerance_px ||" "\n"
    "        (Math.abs(after.width - before.width) > this.C.geometry_tolerance_px"
    " && !this._simContentSized(node)));"
)

#: Injected beside the existing members. ``__MODE__`` is replaced per arm.
_SIM_MEMBER = """
  _simContentSized(node) {
    if (!node || node.nodeType !== 1) return false;
    if (!(node.textContent || '').trim()) return false;
    // The 'box' arm asks only whether the width is content-derived; the others
    // additionally require a text leaf with no block-level child.
    if ('__MODE__' !== 'box') {
      for (const child of node.children) {
        const childDisplay = getComputedStyle(child).display || '';
        if (childDisplay !== 'contents' && !childDisplay.startsWith('inline')) return false;
      }
    }
    if ('__MODE__' === 'box') return this._simShrinkWrapped(node);
    if ('__MODE__' === 'measured') return this._simShrinkWrapped(node);
    const style = getComputedStyle(node);
    const display = style.display || '';
    if (display.startsWith('inline')) return this._simMark(node, 'inline');
    if (style.float && style.float !== 'none') return this._simMark(node, 'float');
    if ('__MODE__' !== 'shrink') return false;
    const parent = node.parentElement;
    if (!parent) return false;
    const parentStyle = getComputedStyle(parent);
    const parentDisplay = parentStyle.display || '';
    if (parentDisplay.endsWith('flex') && !(parentStyle.flexDirection || '').startsWith('column')) {
      const basis = style.flexBasis || 'auto';
      if (basis === 'auto' || basis === 'content') return this._simMark(node, 'flex_item');
    }
    if (parentDisplay.endsWith('grid')) {
      const justify = style.justifySelf && style.justifySelf !== 'auto'
        ? style.justifySelf : (parentStyle.justifyItems || '');
      if (justify && justify !== 'stretch' && justify !== 'normal') return this._simMark(node, 'grid_item');
    }
    return false;
  },
  _simShrinkWrapped(node) {
    // CSS-mechanism agnostic: a box is content-sized when it is exactly as wide
    // as its own inline content *and* narrower than the space its containing
    // block offers. The second half keeps a full-width paragraph out.
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    const pad = parseFloat(style.paddingLeft || 0) + parseFloat(style.paddingRight || 0)
      + parseFloat(style.borderLeftWidth || 0) + parseFloat(style.borderRightWidth || 0);
    const range = document.createRange();
    range.selectNodeContents(node);
    const content = range.getBoundingClientRect().width;
    range.detach && range.detach();
    if (!(content > 0)) return false;
    if (Math.abs(box.width - (content + pad)) > 1.5) return false;
    const parent = node.parentElement;
    if (!parent) return false;
    const parentStyle = getComputedStyle(parent);
    const available = parent.getBoundingClientRect().width
      - parseFloat(parentStyle.paddingLeft || 0) - parseFloat(parentStyle.paddingRight || 0);
    if (!(box.width < available - 1.5)) return false;
    return this._simMark(node, 'shrink_wrapped');
  },
  _simMark(node, why) {
    const qid = node.getAttribute('data-qid') || '?';
    this._simExempt = this._simExempt || {};
    this._simExempt[qid + '|' + why] = (this._simExempt[qid + '|' + why] || 0) + 1;
    return true;
  },
"""


def geometry_for(mode: str) -> str:
    """The geometry fragment for one arm. ``baseline`` returns it untouched."""

    if mode == "baseline":
        return GEOMETRY_MEMBERS
    if _WIDTH_VERDICT not in GEOMETRY_MEMBERS:
        raise SystemExit(
            "the width verdict in slide_capture_fit_geometry.py no longer matches the "
            "text this simulation patches; refusing to measure a guess"
        )
    patched = GEOMETRY_MEMBERS.replace(_WIDTH_VERDICT, _WIDTH_VERDICT_SIM)
    marker = "  _scopeHard(region, baseline, candidate) {"
    if marker not in patched:
        raise SystemExit("_scopeHard is no longer where this simulation injects")
    return patched.replace(marker, _SIM_MEMBER.replace("__MODE__", mode) + marker, 1)


def _walk(obj: Any, key: str, acc: list[Any] | None = None) -> list[Any]:
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == key:
                acc.append(value)
            _walk(value, key, acc)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, key, acc)
    return acc


def presentation_id(run_dir: Path) -> str | None:
    status = json.loads((run_dir / "06-final-status.json").read_text(encoding="utf-8-sig"))
    found = _walk(status, "presentation_id") or _walk(status, "presentationId")
    return found[0] if found else None


def fetch(pid: str, indices: list[int]) -> dict[int, str]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='30s'")
        rows = c.execute(
            "select index, html_content from slides where presentation = %s and index = any(%s)",
            (pid, indices),
        ).fetchall()
    return {int(index): html for index, html in rows if html}


def categories(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in blocking_safety_issues(report):
        name = str(getattr(issue, "category", "?"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def shrink_of(report: dict[str, Any]) -> dict[str, Any]:
    shrunk, minimum = [], None
    for run in report.get("runs") or []:
        final, original = run.get("final_pt"), run.get("original_pt")
        if isinstance(final, (int, float)):
            minimum = final if minimum is None else min(minimum, final)
        if (isinstance(final, (int, float)) and isinstance(original, (int, float))
                and original > 0 and final < original - 1e-6):
            shrunk.append(round(1 - final / original, 6))
    return {"worst_shrink": max(shrunk) if shrunk else 0.0, "min_final_pt": minimum}


def rejection_counts(report: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in report.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("outcome") or "").startswith("removed:duplicate"):
            continue
        rejection = candidate.get("rejection") or {}
        key = str(rejection.get("rule") or rejection.get("code") or candidate.get("outcome") or "-")
        counts[key] = counts.get(key, 0) + 1
    return counts


async def run_arm(mode: str, run: str, index: int, html: str,
                  before: dict[str, int]) -> dict[str, Any]:
    fit_script.GEOMETRY_MEMBERS = geometry_for(mode)
    digest = html_digest(html)
    policy = local_fit_policy(
        author_revision=digest, original_html_digest=digest, slide_index=index,
    )
    fitted = await render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    )
    report = fitted.fit_report or {}
    row: dict[str, Any] = {
        "arm": mode,
        "fit_status": report.get("status"),
        "unresolved": list(report.get("unresolved_reasons") or []),
        "rules": rejection_counts(report),
        **shrink_of(report),
    }
    if not fitted.fitted_html or fitted.fitted_html == html:
        row.update(after=before, verdict="unchanged")
        return row
    verified = await render_html_to_png_with_metrics(
        fitted.fitted_html, timeout=60, fit=False,
        fit_policy={**policy, "baseline": report.get("baseline") or {}}, fresh=True,
    )
    after = categories(inspect_authored_dom(fitted.fitted_html, verified.browser_metrics))
    total_before, total_after = sum(before.values()), sum(after.values())
    row.update(
        after=after,
        verdict=("resolved" if total_after == 0 and total_before > 0
                 else "improved" if total_after < total_before
                 else "worse" if total_after > total_before else "no_change"),
    )
    return row


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root, out = Path(argv[0]), Path(argv[1])
    arms = tuple(os.environ.get("SIM_ARMS", "baseline,inline,shrink,measured").split(","))
    results: list[dict[str, Any]] = []
    for run, indices in TARGETS.items():
        pid = presentation_id(root / run)
        if not pid:
            print(f"{run}: no presentation id", flush=True)
            continue
        htmls = fetch(pid, indices)
        for index in indices:
            html = htmls.get(index)
            if not html:
                print(f"{run} s{index}: no stored html", flush=True)
                continue
            fit_script.GEOMETRY_MEMBERS = GEOMETRY_MEMBERS
            digest = html_digest(html)
            policy = local_fit_policy(
                author_revision=digest, original_html_digest=digest, slide_index=index,
            )
            shipped = await render_html_to_png_with_metrics(
                html, timeout=60, fit=False, fit_policy=policy, fresh=True,
            )
            before = categories(inspect_authored_dom(html, shipped.browser_metrics))
            record: dict[str, Any] = {
                "run": run, "slide": index, "before": before, "arms": [],
            }
            for mode in arms:
                arm = await run_arm(mode, run, index, html, before)
                record["arms"].append(arm)
                print(
                    f"{run} s{index:>2} {mode:<9} {str(arm['fit_status']):<12}"
                    f" {arm['verdict']:<10} after={arm['after']}"
                    f" worst_shrink={arm['worst_shrink']:.3f}"
                    f" min_pt={arm['min_final_pt']}",
                    flush=True,
                )
            results.append(record)
    fit_script.GEOMETRY_MEMBERS = GEOMETRY_MEMBERS
    out.write_text(  # noqa: ASYNC240 - one-shot measurement script, not a server path
        json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== (c) content-width exemption, 9 failing slides ===")
    for mode in arms:
        verdicts: dict[str, int] = {}
        fixed_horizontal = 0
        worst = 0.0
        floor = None
        for record in results:
            arm = next(item for item in record["arms"] if item["arm"] == mode)
            verdicts[arm["verdict"]] = verdicts.get(arm["verdict"], 0) + 1
            fixed_horizontal += arm["rules"].get("fixed_horizontal", 0)
            worst = max(worst, arm["worst_shrink"])
            if isinstance(arm["min_final_pt"], (int, float)):
                floor = arm["min_final_pt"] if floor is None else min(floor, arm["min_final_pt"])
        print(f"  {mode:<9} {verdicts} fixed_horizontal_rejections={fixed_horizontal}"
              f" worst_shrink={worst:.3f} min_pt={floor}")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
