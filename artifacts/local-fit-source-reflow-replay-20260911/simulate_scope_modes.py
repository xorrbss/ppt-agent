"""Scope-level reflow modes, simulated in-process on two baselines.

Implements the four arms of ``docs/authored-local-fit-scope-reflow-design-20260912.md``
by patching the fitter's browser fragments in this process only. Nothing under
``servers/`` changes.

    vertical               production ``authored-local-fit/v1.5``, unpatched (control)
    horizontal_within_root interior boxes of a scope whose layout is a horizontal
                           flow may change left/width; the root box may not
    root_growth            an auto-height container may be a scope root and the
                           root box may grow downward within the slack measured
                           below it at baseline; slack reclaim also looks at
                           preceding siblings and ink-less sections
    horizontal_and_growth  both

Every other check in ``_scopeHard`` stays exactly as shipped: outside boxes,
escapes, overlap, clipping, occlusion, font floor, the vertical movement cap.

The horizontal predicate here is deliberately the *widest* structural reading
the design allows -- a scope is offered the mode when its root, or any ancestor
of a defect leaf inside the root, is a flex row or a grid -- so the result is an
upper bound on what the narrower predicate in the design could yield.

Targets: the 9 verbatim slides the replay left unresolved and the 177
non-verbatim slides whose search ran and ended ``candidates_exhausted``.
Verdicts use the gate the replay used. Read-only: stored HTML in, Chromium
render, JSON out. No generation, no provider call, no database write, no stack
contact.

    python simulate_scope_modes.py <validation-dir> <nonverbatim-targets.json> <out.json>

Resumable: a rerun with the same output skips finished slides.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
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
from utils.slide_capture_fit_parent import PARENT_MEMBERS  # noqa: E402

DB_URL = Path(os.environ.get("LOCAL_FIT_DB_URL_FILE")
              or Path(__file__).resolve().parent / "dburl.txt")
ARMS = ("vertical", "horizontal_within_root", "root_growth", "horizontal_and_growth")
VERBATIM: dict[str, list[int]] = {"run-11": [16, 26, 29], "run-15": [21, 24], "run-16": [9, 19, 25, 26]}


# -- patches -------------------------------------------------------------------

def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"anchor for {label} not found exactly once; refusing to measure a guess")
    return text.replace(old, new, 1)


_SIM_MEMBERS = r"""
  _simMode(region) {
    if (region._simMode !== undefined) return region._simMode;
    const root = region.card, flowy = (node) => {
      const s = getComputedStyle(node), d = s.display || '';
      return d.endsWith('grid') || (d.endsWith('flex') && !(s.flexDirection || '').startsWith('column'));
    };
    let offered = flowy(root);
    for (const leaf of region.defectLeaves || []) {
      for (let n = leaf.parentElement; n && n !== root && root.contains(n); n = n.parentElement)
        if (flowy(n)) offered = true;
      if (offered) break;
    }
    region._simMode = offered ? 'horizontal' : 'vertical';
    this._last.sim_modes = this._last.sim_modes || [];
    this._last.sim_modes.push({root_qid: root.getAttribute('data-qid'), horizontal_offered: offered});
    return region._simMode;
  },
  _simRootSafeBottom(root) {
    const parent = root.parentElement;
    if (!parent || parent === document.body || parent === document.documentElement) return innerHeight;
    return this._availableBottom(root, parent, new Set([root]));
  },
  _simRootGrowthOk(root, before, after, safeBottom) {
    const tol = this.C.geometry_tolerance_px, cap = before.height * 0.10;
    return ['left', 'top', 'width'].every(key => Math.abs(after[key] - before[key]) <= tol) &&
      after.height >= before.height - tol && after.height - before.height <= cap + tol &&
      after.bottom <= safeBottom + tol;
  },
"""

_HORIZONTAL_OLD = (
    "      const horizontal = !labelMorph && (Math.abs(after.left - before.left)"
    " > this.C.geometry_tolerance_px ||\n"
    "        Math.abs(after.width - before.width) > this.C.geometry_tolerance_px);"
)
_HORIZONTAL_NEW = (
    "      const simInterior = __H__ && this._simMode(region) === 'horizontal'"
    " && node !== root && root.contains(node);\n"
    "      const horizontal = !labelMorph && !simInterior && (Math.abs(after.left - before.left)"
    " > this.C.geometry_tolerance_px ||\n"
    "        Math.abs(after.width - before.width) > this.C.geometry_tolerance_px);"
)
_ROOTBOX_OLD = (
    "    if (!this._sameBox(baseline.rootBox, this._box(root)) &&\n"
    "        !this._rootGrowthHard(root, baseline.rootBox, candidate && candidate.rootGrowth))"
    " return {ok: false, reason: 'outside_geometry'};"
)
_ROOTBOX_NEW = (
    "    if (__G__ && baseline._simSafeBottom === undefined) baseline._simSafeBottom = this._simRootSafeBottom(root);\n"
    "    if (!this._sameBox(baseline.rootBox, this._box(root)) &&\n"
    "        !this._rootGrowthHard(root, baseline.rootBox, candidate && candidate.rootGrowth) &&\n"
    "        !(__G__ && this._simRootGrowthOk(root, baseline.rootBox, this._box(root), baseline._simSafeBottom)))"
    " return {ok: false, reason: 'outside_geometry'};"
)
_SCOPEHARD_ANCHOR = "  _scopeHard(region, baseline, candidate) {"

_ELIGIBLE_OLD = "        item.explicitHeight === null || !['block', 'flex', 'grid'].includes(s.display) ||"
_ELIGIBLE_NEW = "        (!__G__ && item.explicitHeight === null) || !['block', 'flex', 'grid'].includes(s.display) ||"
_SHAREDGRID_OLD = (
    "      const sharedGrid = nodes.every(node => node.parentElement === grid) && grid !== root &&\n"
    "        getComputedStyle(grid).display === 'grid' && this._struct(grid).explicitHeight !== null;"
)
_SHAREDGRID_NEW = (
    "      const sharedGrid = nodes.every(node => node.parentElement === grid) && grid !== root &&\n"
    "        getComputedStyle(grid).display === 'grid' && (__G__ || this._struct(grid).explicitHeight !== null);"
)
_FOLLOWING_OLD = (
    "      const following = siblings.slice(siblings.indexOf(grid) + 1).filter(node => node.hasAttribute('data-qid'));"
)
_FOLLOWING_NEW = (
    "      const following = (__G__ ? siblings.filter(node => node !== grid)"
    " : siblings.slice(siblings.indexOf(grid) + 1))"
    ".filter(node => node.hasAttribute('data-qid'));"
)
_INKS_OLD = "        if (!inks.length || this._struct(node).explicitHeight === null) continue;"
_INKS_NEW = (
    "        if (this._struct(node).explicitHeight === null) continue;\n"
    "        if (!inks.length && !__G__) continue;"
)
_MINIMUM_OLD = (
    "        const minimum = Math.max(...inks.map(ink => ink.bottom))"
    " - Math.min(...inks.map(ink => ink.top)) +"
)
_MINIMUM_NEW = (
    "        const minimum = (inks.length ? Math.max(...inks.map(ink => ink.bottom))"
    " - Math.min(...inks.map(ink => ink.top)) : 0) +"
)


def fragments_for(arm: str) -> tuple[str, str]:
    """(GEOMETRY_MEMBERS, PARENT_MEMBERS) for one arm; the control is untouched."""

    if arm == "vertical":
        return GEOMETRY_MEMBERS, PARENT_MEMBERS
    horizontal = arm in ("horizontal_within_root", "horizontal_and_growth")
    growth = arm in ("root_growth", "horizontal_and_growth")
    flags = {"__H__": "true" if horizontal else "false", "__G__": "true" if growth else "false"}

    def fill(text: str) -> str:
        for key, value in flags.items():
            text = text.replace(key, value)
        return text

    geometry = _replace_once(GEOMETRY_MEMBERS, _HORIZONTAL_OLD, fill(_HORIZONTAL_NEW), "horizontal rule")
    geometry = _replace_once(geometry, _ROOTBOX_OLD, fill(_ROOTBOX_NEW), "root box rule")
    geometry = _replace_once(geometry, _SCOPEHARD_ANCHOR, _SIM_MEMBERS + _SCOPEHARD_ANCHOR, "_scopeHard anchor")
    parent = _replace_once(PARENT_MEMBERS, _ELIGIBLE_OLD, fill(_ELIGIBLE_NEW), "root eligibility")
    parent = _replace_once(parent, _SHAREDGRID_OLD, fill(_SHAREDGRID_NEW), "sharedGrid")
    parent = _replace_once(parent, _FOLLOWING_OLD, fill(_FOLLOWING_NEW), "following siblings")
    parent = _replace_once(parent, _INKS_OLD, fill(_INKS_NEW), "ink-less sections")
    parent = _replace_once(parent, _MINIMUM_OLD, _MINIMUM_NEW, "minimum height")
    return geometry, parent


def install(arm: str) -> None:
    fit_script.GEOMETRY_MEMBERS, fit_script.PARENT_MEMBERS = fragments_for(arm)


# -- data -----------------------------------------------------------------------

def resume_from(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {f"{r['deck']}:{r['slide']}": r for r in read_json(path)}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


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


def verbatim_targets(root: Path) -> list[dict[str, Any]]:
    targets = []
    for run, indices in VERBATIM.items():
        status = json.loads((root / run / "06-final-status.json").read_text(encoding="utf-8-sig"))
        found = _walk(status, "presentation_id") or _walk(status, "presentationId")
        if not found:
            continue
        for index in indices:
            targets.append({"corpus": "verbatim", "deck": str(found[0]), "title": run, "slide": index})
    return targets


def fetch(targets: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    wanted: dict[str, list[int]] = defaultdict(list)
    for item in targets:
        wanted[item["deck"]].append(int(item["slide"]))
    htmls: dict[tuple[str, int], str] = {}
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='180s'")
        for pid, indices in wanted.items():
            rows = c.execute(
                "select index, html_content from slides where presentation = %s and index = any(%s)",
                (pid, indices),
            ).fetchall()
            for index, html in rows:
                if html:
                    htmls[(pid, int(index))] = html
    return htmls


# -- measurement ----------------------------------------------------------------

def categories(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in blocking_safety_issues(report):
        name = str(getattr(issue, "category", "?"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def rules_of(report: dict[str, Any]) -> tuple[dict[str, int], int]:
    rules: Counter = Counter()
    judged = 0
    for candidate in report.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("outcome") or "").startswith("removed:duplicate"):
            continue
        judged += 1
        rejection = candidate.get("rejection") or {}
        if rejection:
            rules[str(rejection.get("rule") or rejection.get("code") or "?")] += 1
    return dict(rules), judged


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


async def run_arm(arm: str, index: int, html: str, before: dict[str, int]) -> dict[str, Any]:
    install(arm)
    digest = html_digest(html)
    policy = local_fit_policy(author_revision=digest, original_html_digest=digest, slide_index=index)
    fitted = await render_html_to_png_with_metrics(html, timeout=60, fit=True, fit_policy=policy, fresh=True)
    report = fitted.fit_report or {}
    rules, judged = rules_of(report)
    modes = report.get("sim_modes") or []
    row: dict[str, Any] = {
        "arm": arm,
        "status": report.get("status"),
        "unresolved": list(report.get("unresolved_reasons") or []),
        "candidates_judged": judged,
        "rules": rules,
        "measurements": (report.get("telemetry") or {}).get("measurements"),
        "scopes_offered_horizontal": sum(1 for m in modes if m.get("horizontal_offered")),
        "scopes": len(report.get("scopes") or []),
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
    row.update(after=after, verdict=(
        "resolved" if total_after == 0 and total_before > 0
        else "improved" if total_after < total_before
        else "worse" if total_after > total_before else "no_change"))
    return row


async def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    validation, nonverbatim, out = Path(argv[0]), Path(argv[1]), Path(argv[2])
    for arm in ARMS:
        fragments_for(arm)  # every anchor must match before a single render
    targets = verbatim_targets(validation) + read_json(nonverbatim)
    htmls = fetch(targets)
    done = resume_from(out)
    if done:
        print(f"resuming: {len(done)} slides done", flush=True)
    results: list[dict[str, Any]] = list(done.values())
    for position, item in enumerate(targets, start=1):
        key = f"{item['deck']}:{item['slide']}"
        if key in done:
            continue
        html = htmls.get((item["deck"], int(item["slide"])))
        if not html:
            results.append({**item, "status": "missing_html"})
            continue
        try:
            install("vertical")
            digest = html_digest(html)
            policy = local_fit_policy(
                author_revision=digest, original_html_digest=digest, slide_index=int(item["slide"]),
            )
            shipped = await render_html_to_png_with_metrics(html, timeout=60, fit=False, fit_policy=policy, fresh=True)
            before = categories(inspect_authored_dom(html, shipped.browser_metrics))
            arms = [await run_arm(arm, int(item["slide"]), html, before) for arm in ARMS]
            results.append({**item, "before": before, "arms": arms})
        except Exception as error:  # a slide that will not render is data, not a stop
            results.append({**item, "status": "render_error", "error": str(error)[:160]})
        finally:
            install("vertical")
        if position % 10 == 0 or position == len(targets):
            write_json(out, results)
            print(f"[{position:>3}/{len(targets)}]", flush=True)
    write_json(out, results)

    print("\n=== scope reflow modes ===")
    for corpus in ("verbatim", "non-verbatim"):
        rows = [r for r in results if r.get("corpus") == corpus and "arms" in r]
        print(f"\n{corpus}: {len(rows)} slides")
        for arm in ARMS:
            verdicts = Counter()
            fh = 0
            worst = 0.0
            offered = 0
            for r in rows:
                a = next(x for x in r["arms"] if x["arm"] == arm)
                verdicts[a["verdict"]] += 1
                fh += a["rules"].get("fixed_horizontal", 0)
                worst = max(worst, a["worst_shrink"])
                offered += 1 if a["scopes_offered_horizontal"] else 0
            print(f"  {arm:<24} {dict(verdicts)} fixed_horizontal={fh} worst_shrink={worst:.3f}"
                  f" slides_with_horizontal_scope={offered}")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
