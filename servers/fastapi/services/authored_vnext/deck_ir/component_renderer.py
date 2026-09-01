"""Fixed markup for typed content. Unsupported payloads fail, never disappear."""

from __future__ import annotations

from html import escape
import math
from typing import Any, Mapping

from .grammar import DeckIrGrammarError


def render_component(slide_key: str, component: Mapping[str, Any], *, connector_direction: str | None = None) -> tuple[str, list[dict[str, Any]]]:
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

    css = f"dir-component dir-{kind} dir-role-{role}"
    if kind == "text":
        text = payload["text"]
        attrs = node(qid, text)
        return f'<p {attrs} class="{escape(css, quote=True)}">{escape(text)}</p>', elements
    # Containers are paint hosts, not semantic text. All visible payload fields
    # have their own leaf QID and can be compared to browser readback.
    attrs = node(qid, "", shape=True, semantic_role="decoration")
    body = ""
    if kind == "metric":
        body = leaf(payload["value"], "strong", "dir-metric-value")
        if payload.get("unit"):
            body += leaf(payload["unit"])
        body += leaf(payload["label"], "span", "dir-metric-label")
    elif kind == "card":
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
    elif kind in {"process", "timeline"}:
        body = container("ol", "dir-sequence")
        for index, item in enumerate(payload["steps" if kind == "process" else "events"]):
            body += container("li", "dir-step")
            if kind == "timeline":
                body += leaf(item["time"], "strong", "dir-badge")
            body += leaf(item["label"], "strong")
            if item.get("detail"):
                body += leaf(item["detail"], "p")
            body += "</li>"
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
            body += leaf(payload["caption"], "strong")
        for item in series:
            body += leaf(item["name"], "p", "dir-series-name")
            for category, raw_value in zip(categories, item["values"]):
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
                body += "</div>" + leaf(f"{value:g}") + "</div>"
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
.dir-card{padding:14px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px 10px;}
.dir-card>.dir-badge{grid-column:2;grid-row:1;font-size:15px;margin:0;}
.dir-card>strong{grid-column:1;grid-row:1;}.dir-card>p{grid-column:1/-1;margin:0;}
.dir-metric{padding:22px;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;gap:8px;}
.dir-metric-value{font-size:52px;color:#1463FF;line-height:1.1;}
.dir-metric-label{font-size:20px;color:#102A43;}
.dir-sequence{display:flex;gap:16px;list-style:none;margin:0;padding:0;}
.dir-step{flex:1;min-width:0;padding:16px;background:#EEF4FF;border-top:5px solid #1463FF;}
.dir-badge{color:#0B4BCC;margin-bottom:14px;}.dir-step strong{display:block;}
.dir-data-table{border-collapse:collapse;width:100%;table-layout:fixed;background:white;}
.dir-data-table th,.dir-data-table td{padding:12px 14px;text-align:left;border-bottom:1px solid #C9D6E2;}
.dir-data-table th{background:#102A43;color:white;}.dir-data-table tr:nth-child(even){background:#EEF4FF;}
.dir-data-table caption{text-align:left;font-weight:700;margin-bottom:16px;}
.dir-comparison-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px;}
.dir-bar-row{display:grid;grid-template-columns:minmax(80px,1fr) minmax(0,4fr) 64px;align-items:center;gap:14px;margin-top:16px;}
.dir-bar-track{display:flex;height:32px;background:#E4EDFA;}.dir-bar{display:block;height:100%;background:#1463FF;}
.dir-negative,.dir-positive{width:50%;height:100%;}.dir-negative{display:flex;justify-content:flex-end;border-right:2px solid #102A43;}
.dir-shape{padding:16px;border:2px solid #1463FF;background:#EEF4FF;}
.dir-shape-circle,.dir-shape-pill{border-radius:999px;}.dir-shape-line{border-width:0 0 3px;}
.dir-shape-arrow{border-right:12px solid #1463FF;}.dir-ambient-mark{display:block;width:64px;height:8px;background:#1463FF;}
.dir-connector{display:flex;flex-direction:column;gap:10px;align-items:stretch;padding:4px 0;}
.dir-connector-label{text-align:center;}.dir-connector-arrow{display:block;height:12px;background:#1463FF;clip-path:polygon(0 35%,88% 35%,88% 0,100% 50%,88% 100%,88% 65%,0 65%);}
.dir-connector-left .dir-connector-arrow{transform:rotate(180deg);}
.dir-connector-down .dir-connector-arrow,.dir-connector-up .dir-connector-arrow{width:12px;height:32px;align-self:center;clip-path:polygon(35% 0,65% 0,65% 72%,100% 72%,50% 100%,0 72%,35% 72%);}
.dir-connector-up .dir-connector-arrow{transform:rotate(180deg);}
"""
