"""The slot contract shown to the authoring model for a figure slide.

The prose is deliberately about what the model may still decide — where the
figure sits, which icon each node carries, the few free text slots of a given
archetype — because everything else was decided by the plan and is handed
over as finished markup by ``skeleton.render_skeleton``.
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic

from utils.authored_infographic.icons import icon_reference_lines
from utils.authored_infographic.skeleton import render_skeleton

_ARCHETYPE_NOTES: dict[str, str] = {
    "process": "The arrows between the nodes are the empty .ig-arrow blocks; keep them in place.",
    "cycle": (
        "The ring and the .ig-ring-head arrowheads between the stations are drawn by the "
        "library; keep every one of them. The .ig-hub in the middle carries the takeaway. You may "
        "add one <div class=\"ig-hub-sub\"> line (<= 30 characters) after .ig-hub-title saying "
        "why the loop matters."
    ),
    "hub_spoke": "The first planned node is the hub; the spokes are the empty .ig-spoke elements.",
    "hierarchy": "The first planned node is the root; every other node is a child under the bar.",
    "layers": "Layers stack top to bottom in the planned order; the last layer is the foundation.",
    "funnel": "Bands narrow downward in the planned order; keep the f1..fN classes as given.",
    "pyramid": "Bands widen downward in the planned order; keep the f1..fN classes as given.",
    "timeline": ".ig-when is the date or period of each event; write it from CONTENT when the plan gave none.",
    "roadmap": (
        "Each phase has a bar: choose its offset class o0/o20/o40/o60/o80 and width class "
        "w20/w40/w60/w80/w100 from the durations in CONTENT so the bars read as a schedule "
        "(offset + width at most 100; the library clips anything longer)."
    ),
    "swimlane": (
        "Each lane's .ig-step chips are its steps; rewrite them from CONTENT (<= 8 characters each, "
        "at most 5 per lane) and keep one empty .ig-step-arrow between chips."
    ),
    "branch": "The first planned node is the decision; the others are its outcomes on the right.",
    "comparison": (
        "This is a real <table>: the header cells are the options. Add two to four criteria rows "
        "<tr data-qid-ignore=\"\"><th scope=\"row\" data-qid-ignore=\"\">criterion</th>"
        "<td data-qid-ignore=\"\">...</td>...</tr> to <tbody> from CONTENT, one <td> per option, "
        "and replace the CRITERION placeholder with the criterion the figures belong to. "
        "Mark the decisive cell with class is-key when CONTENT supports it. Every row and cell you "
        "add carries data-qid-ignore and no data-qid, and no cell holds an icon or an image."
    ),
    "before_after": (
        "This is a real <table>. Write the header words (ITEM, BEFORE, AFTER and, when present, "
        "DELTA) in the deck language as CONTENT names them; the row cells are already filled "
        "from the plan's before/after values. Keep every data-qid-ignore, and never put an icon "
        "or an image in a cell."
    ),
    "comparison_values": (
        "The BEFORE / AFTER row headers are placeholders: write them in the deck language as "
        "CONTENT names the two moments or measures."
    ),
    "kpi": "Each tile is one stated figure with its unit; never invent or round a number.",
    "gauge": (
        "Pick the dial class v10..v100 on .ig-dial-ring that matches the first node's share "
        "(v70 for 72%). The other nodes are the facts beside the dial."
    ),
    "icon_list": "Give every node its own icon; the labels stay exactly as planned.",
    "checklist": "Set each node to is-done or is-open as CONTENT states; the check mark is drawn by the library.",
    "matrix": (
        "Replace AXIS Y / AXIS X / LOW with the two axis names and their low end from CONTENT, "
        "replace each QUADRANT tag with a two- to four-word name, and move each .ig-node into the "
        ".ig-q quadrant CONTENT places it in (q-tr is high/high)."
    ),
    "venn": "The circles are drawn by the library; the .ig-core or the fourth node names the overlap.",
    "scene": (
        "Write the img data-illust-prompt exactly as the ILLUSTRATION SLOT rules require (an English "
        "SCENE of the real object CONTENT names, labels verbatim from CONTENT), keep its px "
        "width/height, and place each .ig-co callout and its .ig-pin around the object by editing "
        "only their left/top/width/height px values."
    ),
}


def slot_contract(slide: AuthoredSlideInfographic) -> str:
    """The figure contract for one planned figure slide."""
    skeleton = render_skeleton(slide)
    note = _ARCHETYPE_NOTES[slide.archetype]
    if slide.archetype == "comparison" and any(n.before or n.after for n in slide.nodes):
        note += " " + _ARCHETYPE_NOTES["comparison_values"]
    return (
        "FIGURE CONTRACT (the figure below is finished markup; reproduce it, do not redesign it):\n"
        "1. Paste the following figure into the slide body EXACTLY as given — every element, class, "
        "data-qid, data-native, data-qid-ignore and text — at the place your layout reserves for it. "
        "Give its container an "
        "explicit width and height (the whole content area for a HERO page, at least 700x380px "
        "otherwise); the figure fills that box.\n"
        "2. The figure's stylesheet is injected by the server. Do NOT write CSS for any .ig* class, "
        "do NOT add a class the library does not define, and do NOT put a style attribute or a "
        "<style> element inside the figure (the only exception: left/top/width/height px on "
        ".ig-co / .ig-pin and the img of a scene).\n"
        "3. You MAY: swap any icon inside an .ig-ic for another from the ICON MENU; rewrite the "
        "UPPERCASE placeholder words in the deck language; and do what the ARCHETYPE NOTE allows. "
        "Every other text is verbatim from the plan — never reword, translate, add or drop a label.\n"
        "4. Everything outside the figure (header, eyebrow, page number, any supporting text) is "
        "yours to author under the design system as usual, but no other diagram, card grid or "
        "chart may compete with the figure.\n"
        f"ARCHETYPE NOTE ({slide.archetype}): {note}\n"
        "FIGURE MARKUP:\n"
        f"{skeleton}\n"
        "ICON MENU (paste one <svg> verbatim inside an .ig-ic; no fill/stroke/class/style attributes):\n"
        f"{icon_reference_lines()}"
    )


__all__ = ["slot_contract"]
