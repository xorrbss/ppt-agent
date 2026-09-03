"""Fixed markup for typed content. Unsupported payloads fail, never disappear."""

from __future__ import annotations

from html import escape
import math
from typing import Any, Mapping

from .grammar import DeckIrGrammarError


def render_component(slide_key: str, component: Mapping[str, Any], *, connector_direction: str | None = None, narrow: bool = False) -> tuple[str, list[dict[str, Any]]]:
    key, kind = component["elementKey"], component["kind"]
    role, payload = component["semanticRole"], component["payload"]
    qid = f"{slide_key}.{key}"
    elements: list[dict[str, Any]] = []

    def node(identity: str, text: str, *, shape: bool = False, semantic_role: str = role) -> str:
        elements.append({"qid": identity, "elementKey": identity[len(slide_key) + 1:],
                         "kind": "shape" if shape else "text", "semanticRole": semantic_role,
                         "text": text, "assetSlot": None, "dataDigest": component.get("dataDigest")})
        return f'data-qid="{escape(identity, quote=True)}" data-semantic-role="{escape(semantic_role, quote=True)}"'

    def leaf(value: Any, tag: str = "span", css: str = "") -> str:
        text = "" if value is None else str(value)
        identity = f"{qid}.part{len(elements):02d}"
        attrs = node(identity, text, shape=not text, semantic_role=role if text else "decoration")
        return f'<{tag} {attrs} class="dir-leaf {css}">{escape(text)}</{tag}>'

    def container(tag: str, css: str = "") -> str:
        attrs = node(f"{qid}.node{len(elements):02d}", "", shape=True, semantic_role="decoration")
        return f'<{tag} {attrs} class="{css}">'

    variant = component.get("variant")
    conditional_gate = kind == "process" and variant == "conditional-gate"
    css = f"dir-component dir-{kind} dir-role-{role}"
    if narrow:
        css += " dir-component-narrow"
    if conditional_gate:
        css += " dir-variant-conditional-gate"
    if kind == "text":
        text = payload["text"]
        attrs = node(qid, text)
        return f'<p {attrs} class="{escape(css, quote=True)}">{escape(text)}</p>', elements
    # Containers are paint hosts, not semantic text. All visible payload fields
    # have their own leaf QID and can be compared to browser readback.
    attrs = node(qid, "", shape=True, semantic_role="decoration")
    body = ""
    if kind == "metric":
        body = '<i class="dir-metric-rule" data-semantic-role="decoration" aria-hidden="true"></i>'
        body += leaf(payload["value"], "strong", "dir-metric-value")
        if payload.get("unit"):
            body += leaf(payload["unit"], "span", "dir-metric-unit")
        body += leaf(payload["label"], "span", "dir-metric-label")
    elif kind == "card":
        body = '<i class="dir-card-rule" data-semantic-role="decoration" aria-hidden="true"></i>'
        if payload.get("badge"):
            body += leaf(payload["badge"], "span", "dir-badge")
        body += leaf(payload["title"], "strong") + leaf(payload["body"], "p")
    elif kind == "quote":
        body = leaf(payload["quote"], "blockquote")
        if payload.get("attribution"):
            body += leaf(payload["attribution"], "p")
    elif kind == "comparison":
        if payload.get("axisLabel"):
            body += leaf(payload["axisLabel"], "strong")
        body += container("div", "dir-comparison-columns")
        for field in ("leftItems", "rightItems"):
            body += container("ul") + "".join(leaf(item, "li") for item in payload[field]) + "</ul>"
        body += "</div>"
    elif conditional_gate:
        items = payload["steps"]
        if len(items) != 3:
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        body = container("div", "dir-conditional-gate")
        body += container("article", "dir-gate-entry")
        body += leaf(items[0]["label"], "strong", "dir-step-title")
        if items[0].get("detail"):
            body += leaf(items[0]["detail"], "p", "dir-step-detail")
        body += "</article>"
        body += '<i class="dir-gate-stem" data-semantic-role="decoration" aria-hidden="true"></i>'
        body += container("div", "dir-gate-outcomes")
        body += '<i class="dir-gate-branch" data-semantic-role="decoration" aria-hidden="true"></i>'
        for index, item in enumerate(items[1:]):
            state = "dir-gate-outcome-positive" if index == 0 else "dir-gate-outcome-negative"
            body += container("article", f"dir-gate-outcome {state}")
            body += '<i class="dir-gate-outcome-dot" data-semantic-role="decoration" aria-hidden="true"></i>'
            body += leaf(item["label"], "strong", "dir-step-title")
            if item.get("detail"):
                body += leaf(item["detail"], "p", "dir-step-detail")
            body += "</article>"
        body += "</div></div>"
    elif kind in {"process", "timeline"}:
        items = payload["steps" if kind == "process" else "events"]
        serpentine = kind == "process" and len(items) in {5, 6} and not narrow
        body = container("ol", "dir-sequence dir-process-serpentine" if serpentine else "dir-sequence")
        if kind == "timeline":
            body += '<i class="dir-timeline-rail" data-semantic-role="decoration" aria-hidden="true"></i>'
        for index, item in enumerate(items):
            position = "dir-step-first" if index == 0 else "dir-step-last" if index == len(items) - 1 else "dir-step-middle"
            step_css = f"dir-step {position}"
            if serpentine:
                step_css += f" dir-step-grid-{index + 1}"
            body += container("li", step_css)
            if kind == "timeline":
                body += '<i class="dir-timeline-dot" data-semantic-role="decoration" aria-hidden="true"></i>'
            # Derived sequence order is visible content, so it receives the
            # same readback identity as every provider payload leaf.
            body += leaf(f"{index + 1:02d}", "span", "dir-step-number")
            if kind == "timeline":
                body += leaf(item["time"], "strong", "dir-badge dir-timeline-time")
            body += leaf(item["label"], "strong", "dir-step-title")
            if item.get("detail"):
                body += leaf(item["detail"], "p", "dir-step-detail")
            body += "</li>"
            if kind == "process" and index != len(items) - 1:
                arrow_css = "dir-sequence-arrow"
                if serpentine:
                    arrow_css += f" dir-sequence-arrow-{index + 1}"
                body += f'<i class="{arrow_css}" data-semantic-role="decoration" aria-hidden="true"></i>'
        body += "</ol>"
    elif kind == "table":
        columns, rows = payload["columns"], payload["rows"]
        if not columns or any(len(row) != len(columns) for row in rows):
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        body = container("table", "dir-data-table")
        if payload.get("caption"):
            body += leaf(payload["caption"], "caption")
        body += container("thead") + container("tr") + "".join(leaf(value, "th") for value in columns) + "</tr></thead>" + container("tbody")
        body += "".join(container("tr") + "".join(leaf(value, "td") for value in row) + "</tr>" for row in rows)
        body += "</tbody></table>"
    elif kind == "chart":
        if payload["chartType"] != "bar":
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        categories, series = payload["categories"], payload["series"]
        if not categories or not series or any(len(item["values"]) != len(categories) for item in series):
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        values = [float(value) for item in series for value in item["values"]]
        if not all(math.isfinite(value) for value in values):
            raise DeckIrGrammarError("deck_ir_component_invalid")
        scale = max([abs(value) for value in values] + [1.0])
        if payload.get("caption"):
            body += leaf(payload["caption"], "strong", "dir-chart-caption")
        for item in series:
            body += leaf(item["name"], "p", "dir-series-name")
            for category, raw_value in zip(categories, item["values"], strict=True):
                value = float(raw_value)
                percent = abs(value) / scale * 100
                # Server-computed finite scalar; provider CSS is never accepted.
                bar = f'<i class="dir-bar" data-semantic-role="decoration" aria-hidden="true" style="width:{percent:.4f}%"></i>'
                body += container("div", "dir-bar-row") + leaf(category) + container("div", "dir-bar-track")
                if any(number < 0 for number in values):
                    body += container("div", "dir-negative") + (bar if value < 0 else "") + "</div>"
                    body += container("div", "dir-positive") + (bar if value >= 0 else "") + "</div>"
                else:
                    body += bar
                body += "</div>" + leaf(f"{value:g}", "strong", "dir-chart-value") + "</div>"
    elif kind == "shape":
        shape_kind = payload["shapeKind"]
        if shape_kind not in {"rect", "circle", "pill", "line", "arrow"}:
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        css += f" dir-shape-{shape_kind}"
        if payload.get("label"):
            body += leaf(payload["label"])
    elif kind == "connector":
        if connector_direction not in {"right", "left", "up", "down"}:
            raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
        css += f" dir-connector-{connector_direction}"
        if payload.get("label"):
            body += leaf(payload["label"], "span", "dir-connector-label")
        body += '<i class="dir-connector-arrow" data-semantic-role="decoration" aria-hidden="true"></i>'
    elif kind == "decoration":
        # Ambient paint lives inside its own non-overlapping zone. The motif is
        # metadata, not arbitrary text/HTML or permission to cover content.
        body = '<i class="dir-ambient-mark" data-semantic-role="decoration" aria-hidden="true"></i>'
    else:
        # Image and nested group renderers require owned asset or
        # geometry bindings. Never substitute a label for the actual content.
        raise DeckIrGrammarError("deck_ir_payload_render_unsupported")
    return f'<section {attrs} class="{escape(css, quote=True)}">{body}</section>', elements


COMPONENT_CSS = """
.dir-component,.dir-component p,.dir-component blockquote{margin:0;}
.dir-leaf{min-width:0;overflow-wrap:anywhere;word-break:keep-all;}
.dir-component strong{display:block;}.dir-component p{margin-top:10px;}
.dir-card{position:relative;overflow:hidden;padding:22px 20px 20px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 14px;align-content:start;border:1px solid #D9E2EF;border-radius:18px;background:#FFF;}
.dir-card-rule{position:absolute;left:20px;top:0;width:38px;height:4px;background:#1473E6;}
.dir-card>.dir-badge{grid-column:2;grid-row:1;align-self:start;font-size:15px;font-weight:750;line-height:1.1;margin:0;padding:7px 9px;border-radius:999px;background:#EAF2FF;color:#1473E6;}
.dir-card>strong{grid-column:1;grid-row:1;font-size:1.18em;line-height:1.28;color:#0F172A;}.dir-card>p{grid-column:1/-1;margin:0;color:#475569;line-height:1.48;}
.dir-card:has(>.dir-badge){border-color:#1473E6;background:#F8FAFC;}
.dir-metric{position:relative;overflow:hidden;padding:28px 24px 24px;display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-rows:auto auto;column-gap:10px;align-content:center;border:0;border-radius:18px;background:#F8FAFC;color:#0F172A;}
.dir-metric-rule{position:absolute;left:24px;right:24px;bottom:16px;height:3px;background:#1473E6;}
.dir-metric-value{grid-column:1;font-size:clamp(44px,5vw,72px);font-weight:800;color:#1473E6;line-height:.98;letter-spacing:-.045em;}
.dir-metric-unit{align-self:end;padding-bottom:8px;color:#1473E6;font-size:1.08em;font-weight:750;}.dir-metric-label{grid-column:1/-1;margin-top:12px;color:#475569;font-size:.9em;font-weight:700;line-height:1.25;}
.dir-process,.dir-timeline{padding:0;border:0;border-radius:0;background:transparent;}
.dir-sequence{display:flex;gap:24px;list-style:none;margin:0;padding:0;}
.dir-process-serpentine{display:grid;grid-template-columns:minmax(0,1fr) 28px minmax(0,1fr) 28px minmax(0,1fr);grid-template-rows:minmax(0,1fr) 30px minmax(0,1fr);gap:14px 8px;}
.dir-process-serpentine .dir-step{min-height:0;}.dir-process-serpentine .dir-step-grid-1{grid-column:1;grid-row:1;}.dir-process-serpentine .dir-step-grid-2{grid-column:3;grid-row:1;}.dir-process-serpentine .dir-step-grid-3{grid-column:5;grid-row:1;}.dir-process-serpentine .dir-step-grid-4{grid-column:5;grid-row:3;}.dir-process-serpentine .dir-step-grid-5{grid-column:3;grid-row:3;}.dir-process-serpentine .dir-step-grid-6{grid-column:1;grid-row:3;}
.dir-process-serpentine .dir-sequence-arrow{align-self:center;justify-self:stretch;}.dir-process-serpentine .dir-sequence-arrow-1{grid-column:2;grid-row:1;}.dir-process-serpentine .dir-sequence-arrow-2{grid-column:4;grid-row:1;}.dir-process-serpentine .dir-sequence-arrow-3{grid-column:5;grid-row:2;width:12px;height:30px;justify-self:center;clip-path:polygon(35% 0,65% 0,65% 72%,100% 72%,50% 100%,0 72%,35% 72%);}.dir-process-serpentine .dir-sequence-arrow-4{grid-column:4;grid-row:3;transform:rotate(180deg);}.dir-process-serpentine .dir-sequence-arrow-5{grid-column:2;grid-row:3;transform:rotate(180deg);}
.dir-step{position:relative;flex:1;min-width:0;}
.dir-process .dir-step{min-height:150px;padding:52px 18px 18px;border:1px solid #D9E2EF;border-radius:18px;background:#F8FAFC;}
.dir-process .dir-process-serpentine .dir-step{min-height:0;padding:16px;display:grid;grid-template-columns:24px minmax(0,1fr);column-gap:8px;align-content:center;}
.dir-process-serpentine .dir-step-number{position:static;align-self:center;}
.dir-process-serpentine .dir-step-title{grid-column:2;}
.dir-process-serpentine .dir-step-detail{grid-column:1/-1;margin-top:8px;padding-top:8px;}
.dir-step-number{position:absolute;left:18px;top:17px;color:#1473E6;font-size:15px;font-weight:800;letter-spacing:.08em;line-height:1;}
.dir-step-title{font-size:1.16em;line-height:1.25;color:#0F172A;}.dir-step-detail{padding-top:11px;border-top:1px solid #D9E2EF;color:#475569;font-size:.9em;line-height:1.4;}
.dir-process .dir-step-first{background:#1473E6;border-color:#1473E6;}.dir-process .dir-step-first .dir-step-number,.dir-process .dir-step-first .dir-step-title,.dir-process .dir-step-first .dir-step-detail{color:#FFF;}.dir-process .dir-step-first .dir-step-detail{border-color:rgba(255,255,255,.38);}
.dir-process .dir-step-last{border:2px solid #1473E6;background:#FFF;}.dir-sequence-arrow{display:block;flex:0 0 10px;align-self:center;height:12px;background:#1473E6;clip-path:polygon(0 35%,64% 35%,64% 0,100% 50%,64% 100%,64% 65%,0 65%);}
.dir-conditional-gate{display:grid;grid-template-columns:1fr;justify-items:center;}.dir-gate-entry{width:min(100%,440px);padding:22px 20px;border-radius:18px;background:#0F172A;color:#FFF;}.dir-gate-entry .dir-step-title{color:#FFF;}.dir-gate-entry .dir-step-detail{border-color:rgba(255,255,255,.34);color:#EAF2FF;}.dir-gate-stem{display:block;width:3px;height:30px;background:#1473E6;}.dir-gate-outcomes{display:grid;grid-template-columns:1fr 1fr;width:100%;gap:18px;padding-top:20px;}.dir-gate-branch{display:block;grid-column:1/-1;height:3px;margin:0 25%;background:#1473E6;}.dir-gate-outcome{min-height:118px;padding:20px 18px;border:1px solid #D9E2EF;border-radius:16px;background:#F8FAFC;}.dir-gate-outcome-dot{display:block;width:10px;height:10px;margin:-26px auto 14px;border:3px solid #1473E6;border-radius:50%;background:#FFF;}.dir-gate-outcome-positive{border-color:#1473E6;background:#1473E6;}.dir-gate-outcome-positive .dir-step-title,.dir-gate-outcome-positive .dir-step-detail{color:#FFF;}.dir-gate-outcome-positive .dir-step-detail{border-color:rgba(255,255,255,.38);}.dir-gate-outcome-positive .dir-gate-outcome-dot{background:#1473E6;}.dir-gate-outcome-negative{border:2px solid #475569;background:#FFF;}
.dir-timeline .dir-sequence{position:relative;gap:18px;padding-top:46px;}.dir-timeline-rail{position:absolute;left:18px;right:18px;top:16px;height:3px;background:#BFD4F2;}
.dir-timeline .dir-step{min-height:260px;padding:58px 18px 42px;border:1px solid #D9E2EF;border-radius:16px;background:#F8FAFC;}.dir-timeline-dot{position:absolute;z-index:1;top:-43px;left:18px;width:14px;height:14px;border:4px solid #1473E6;border-radius:50%;background:#FFF;}
.dir-timeline-time{position:absolute;top:15px;left:18px;margin:0;color:#1473E6;font-size:24px;font-weight:800;letter-spacing:.04em;line-height:1.1;}.dir-timeline .dir-step-title{font-size:1.1em;}.dir-timeline .dir-step-number{top:auto;bottom:17px;}
.dir-timeline .dir-step-last{border-color:#1473E6;background:#1473E6;box-shadow:0 14px 28px rgba(20,115,230,.18);}.dir-timeline .dir-step-last .dir-timeline-dot{background:#1473E6;box-shadow:0 0 0 7px #EAF2FF;}.dir-timeline .dir-step-last .dir-step-number,.dir-timeline .dir-step-last .dir-step-title,.dir-timeline .dir-step-last .dir-step-detail,.dir-timeline .dir-step-last .dir-timeline-time{color:#FFF;}.dir-timeline .dir-step-last .dir-step-detail{border-color:rgba(255,255,255,.38);}
.dir-badge{color:#1473E6;}.dir-data-table{border-collapse:separate;border-spacing:0;width:100%;table-layout:fixed;overflow:hidden;border:1px solid #D9E2EF;border-radius:18px;background:#FFF;}
.dir-data-table th,.dir-data-table td{padding:13px 15px;text-align:left;border-right:1px solid #D9E2EF;border-bottom:1px solid #D9E2EF;vertical-align:middle;}.dir-data-table tr>*:last-child{border-right:0;}.dir-data-table tbody tr:last-child>*{border-bottom:0;}
.dir-data-table th{background:#1473E6;color:#FFF;font-size:.9em;font-weight:800;}.dir-data-table td{color:#0F172A;}.dir-data-table tbody tr:nth-child(even){background:#F8FAFC;}.dir-data-table caption{caption-side:top;padding:0 0 12px;text-align:left;color:#0F172A;font-weight:800;}
.dir-comparison-columns{display:grid;grid-template-columns:1fr 1fr;gap:18px;}.dir-comparison-columns ul{margin:0;padding:16px 18px 16px 34px;border-top:3px solid #1473E6;background:#F8FAFC;}.dir-comparison-columns li+li{margin-top:8px;}
.dir-chart-caption{margin-bottom:9px;color:#0F172A;font-size:1.06em;}.dir-series-name{margin-top:13px!important;color:#475569;font-size:.86em;font-weight:800;letter-spacing:.04em;}.dir-bar-row{display:grid;grid-template-columns:minmax(80px,1.1fr) minmax(0,4fr) minmax(46px,.7fr);align-items:center;gap:12px;margin-top:10px;color:#475569;font-size:.9em;}
.dir-bar-track{display:flex;min-width:0;height:22px;overflow:hidden;border-radius:3px;background:#EAF2FF;}.dir-bar{display:block;height:100%;background:#1473E6;}.dir-chart-value{text-align:right;color:#0F172A;font-size:1.05em;font-variant-numeric:tabular-nums;}
.dir-negative,.dir-positive{width:50%;height:100%;}.dir-negative{display:flex;justify-content:flex-end;border-right:2px solid #0F172A;}.dir-shape{padding:16px;border:2px solid #1473E6;background:#F8FAFC;}
.dir-shape-circle,.dir-shape-pill{border-radius:999px;}.dir-shape-line{border-width:0 0 3px;}
.dir-shape-arrow{border-right:12px solid #1473E6;}.dir-ambient-mark{display:block;width:64px;height:8px;background:#1473E6;}
.dir-connector{display:flex;flex-direction:column;gap:10px;align-items:stretch;padding:4px 0;}
.dir-connector-label{text-align:center;}.dir-connector-arrow{display:block;height:12px;background:#1473E6;clip-path:polygon(0 35%,88% 35%,88% 0,100% 50%,88% 100%,88% 65%,0 65%);}
.dir-connector-left .dir-connector-arrow{transform:rotate(180deg);}
.dir-connector-down .dir-connector-arrow,.dir-connector-up .dir-connector-arrow{width:12px;height:32px;align-self:center;clip-path:polygon(35% 0,65% 0,65% 72%,100% 72%,50% 100%,0 72%,35% 72%);}
.dir-connector-up .dir-connector-arrow{transform:rotate(180deg);}
.composition-chart .dir-process .dir-sequence,.dir-process.dir-component-narrow .dir-sequence{flex-direction:column;gap:16px;}.composition-chart .dir-process .dir-step,.dir-process.dir-component-narrow .dir-step{min-height:0;}.composition-chart .dir-process .dir-sequence-arrow,.dir-process.dir-component-narrow .dir-sequence-arrow{width:12px;height:10px;align-self:center;transform:rotate(90deg);}
.dir-component-narrow .dir-gate-stem{width:50%;height:26px;justify-self:start;background:transparent;border-right:3px solid #1473E6;border-bottom:3px solid #1473E6;}
.dir-component-narrow .dir-gate-outcomes{position:relative;grid-template-columns:1fr;gap:14px;padding:14px 0 0 20px;}.dir-component-narrow .dir-gate-branch{position:absolute;left:0;top:0;bottom:20px;width:3px;height:auto;margin:0;background:#1473E6;}.dir-component-narrow .dir-gate-outcome{position:relative;}.dir-component-narrow .dir-gate-outcome-dot{position:absolute;top:25px;left:-20px;width:20px;height:3px;border:0;border-radius:0;margin:0;background:#1473E6;}
.dir-card>.dir-badge,.dir-step-number,.dir-timeline-time,.dir-metric-unit{color:#0B4BCC;}
.dir-process .dir-step-first,.dir-timeline .dir-step-last,.dir-gate-outcome-positive{color:#FFF;}
.dir-timeline.dir-component-narrow .dir-sequence{flex-direction:column;gap:12px;padding:0 0 0 42px;}.dir-timeline.dir-component-narrow .dir-timeline-rail{left:16px;right:auto;top:18px;bottom:18px;width:3px;height:auto;}.dir-timeline.dir-component-narrow .dir-step{min-height:100px;padding-top:44px;}.dir-timeline.dir-component-narrow .dir-timeline-dot{left:-34px;top:18px;}.dir-timeline.dir-component-narrow .dir-timeline-time{top:14px;font-size:17px;}.dir-timeline.dir-component-narrow .dir-step-number{bottom:14px;}
"""
