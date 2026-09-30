"""Deterministic geometry probe for the archetype component library.

The archetypes that place their plates absolutely — cycle, hub_spoke, venn,
scene — and the roadmap bar are the ones a CSS edit can silently break: a
station lands on the hub, a callout lands on its neighbour, a schedule bar
runs past its track. A browser render is the honest check, but it needs
Chrome, so the invariant is also checked here without one: the library CSS is
resolved for the very elements the skeleton emits, and the resulting boxes
must be disjoint and inside the figure.

The model is exact for everything the CSS states (position, width, declared
height, margins) and uses one measured constant per archetype and size class
for the plate heights the CSS leaves to the content. Those constants come
from the 1152x560 fixture renders in
``artifacts/infographic-fixture-run-20260903/F2-verify`` and are the largest
height observed there, so a plate that grows past its evidence fails here.
"""

import re
from html.parser import HTMLParser

import pytest

from models.authored_infographic_plan import coerce_infographic_plan
from utils.authored_infographic import library_css, render_skeleton
from utils.authored_infographic.skeleton import density_class
from utils.authored_infographic.skeleton_flow import roadmap_bar
from services.generation_core.authoring.author_slide import Brand

#: The figure box the contract asks a hero page to reserve.
CANVAS_W, CANVAS_H = 1152, 560
#: Largest rendered plate height per archetype and size class: the size class
#: is what sets the type scale, so it is what sets the plate height.
PLATE_HEIGHT = {
    "cycle": {"n3": 224, "n4": 208, "n5": 199, "n6": 186},
    "hub_spoke": {"n3": 224, "n4": 208, "n5": 199, "n6": 186},
    "venn": {"n3": 84, "n4": 76, "n5": 76, "n6": 76},
    "scene": {"n3": 88, "n4": 88, "n5": 88, "n6": 88},
}
#: The venn overlap caption is one 20px line in a 200px box.
CORE_HEIGHT = 60
PLATE_CLASSES = ("ig-node", "ig-hub", "ig-co", "ig-core")
_LABELS = [
    ("감지", "센서 이상 패턴 포착", "38시간"),
    ("예측", "고장 확률 산출", "72시간 전"),
    ("조치", "작업지시 자동 발행", "12건"),
    ("학습", "조치 결과 반영", "4.6%"),
    ("확산", "협력사 공유", "9개 사"),
    ("보안", "망분리 3단계", "3단계"),
]
_CONTENT = " ".join(" ".join(row) for row in _LABELS)
_LENGTH = re.compile(r"^calc\(\s*([\d.]+)%\s*([+-])\s*([\d.]+)px\s*\)$")
_BOX_PROPERTIES = ("left", "right", "top", "bottom", "width", "height", "margin-left", "margin-top")


def _slide(archetype: str, count: int, emphasis: int | None = 1):
    plan = coerce_infographic_plan(
        {
            "slides": [
                {
                    "index": 0,
                    "archetype": archetype,
                    "weight": "hero",
                    "takeaway": "손실지표 개선",
                    "nodes": [
                        {"label": label, "desc": desc, "figure": figure}
                        for label, desc, figure in _LABELS[:count]
                    ],
                    "emphasis": emphasis,
                    "density": "high",
                }
            ]
        },
        slide_contents=[_CONTENT],
        roles=["CONTENT"],
    )
    return plan.at(0)


# --- a very small CSS resolver -------------------------------------------------


def _rules(css: str) -> list[tuple[int, int, list[frozenset[str]], dict[str, str]]]:
    """Every rule of the library as (specificity, order, compounds, declarations)."""
    out = []
    for order, block in enumerate(css.split("}")):
        if "{" not in block:
            continue
        selectors, _, body = block.partition("{")
        declarations = {}
        for piece in body.split(";"):
            name, _, value = piece.partition(":")
            if value:
                declarations[name.strip()] = value.strip()
        for selector in " ".join(selectors.split()).split(","):
            compounds = [
                frozenset(part.split(".")[1:]) for part in selector.split() if part.startswith(".")
            ]
            if compounds and len(compounds) == len(selector.split()):
                out.append((sum(len(c) for c in compounds), order, compounds, declarations))
    return out


def _matches(compounds: list[frozenset[str]], chain: list[frozenset[str]]) -> bool:
    """Descendant-combinator match of a class-only selector against a chain."""
    index, position = len(chain) - 1, len(compounds) - 1
    if not compounds[position] <= chain[index]:
        return False
    position -= 1
    index -= 1
    while position >= 0:
        while index >= 0 and not compounds[position] <= chain[index]:
            index -= 1
        if index < 0:
            return False
        position -= 1
        index -= 1
    return True


def _declarations(rules, chain: list[frozenset[str]]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for _, _, _compounds, declarations in sorted(
        (rule for rule in rules if _matches(rule[2], chain)), key=lambda rule: (rule[0], rule[1])
    ):
        resolved.update(declarations)
    return resolved


def _length(value: str, base: float) -> float:
    text = value.strip()
    match = _LENGTH.match(text)
    if match:
        offset = float(match.group(3))
        return base * float(match.group(1)) / 100 + (offset if match.group(2) == "+" else -offset)
    if text.endswith("px"):
        return float(text[:-2])
    if text.endswith("%"):
        return base * float(text[:-1]) / 100
    if float(text) == 0:
        return 0.0
    raise ValueError(f"unsupported CSS length {value!r}")


def _box(declarations: dict[str, str], height: float) -> tuple[float, float, float, float]:
    width = _length(declarations["width"], CANVAS_W)
    # ``height:auto`` is a plate that takes the height of what it holds, which
    # is the case the measured constant is for -- reading it as a length would
    # be reading a number the CSS deliberately does not state.
    if declarations.get("height", "auto") != "auto":
        height = _length(declarations["height"], CANVAS_H)
    if "left" in declarations:
        x = _length(declarations["left"], CANVAS_W) + _length(
            declarations.get("margin-left", "0"), CANVAS_W
        )
    else:
        x = CANVAS_W - _length(declarations["right"], CANVAS_W) - width
    if "top" in declarations:
        y = _length(declarations["top"], CANVAS_H) + _length(
            declarations.get("margin-top", "0"), CANVAS_H
        )
    else:
        y = CANVAS_H - _length(declarations["bottom"], CANVAS_H) - height
    return x, y, width, height


class _Plates(HTMLParser):
    """The figure root's own plates, with their classes and inline geometry."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: frozenset[str] = frozenset()
        self.plates: list[tuple[frozenset[str], dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        values = {k.lower(): (v or "") for k, v in attrs}
        classes = frozenset(values.get("class", "").split())
        if tag == "section" and "ig" in classes:
            self.root = classes
            return
        if not classes.intersection(PLATE_CLASSES):
            return
        inline = {}
        for piece in values.get("style", "").split(";"):
            name, _, value = piece.partition(":")
            if value and name.strip() in _BOX_PROPERTIES:
                inline[name.strip()] = value.strip()
        self.plates.append((classes, inline))


def _plate_boxes(archetype: str, count: int, emphasis: int | None = 1):
    slide = _slide(archetype, count, emphasis)
    parser = _Plates()
    parser.feed(render_skeleton(slide))
    parser.close()
    rules = _rules(library_css(Brand(topic="스마트팩토리", primary="#1E5EFF", fonts="Pretendard")))
    boxes = []
    for classes, inline in parser.plates:
        declarations = _declarations(rules, [parser.root, classes])
        declarations.update(inline)
        if declarations.get("position") == "static" or "width" not in declarations:
            continue
        height = (
            CORE_HEIGHT
            if "ig-core" in classes
            else PLATE_HEIGHT[archetype][density_class(slide)]
        )
        boxes.append((" ".join(sorted(classes)), _box(declarations, height)))
    return boxes


# --- the invariants ------------------------------------------------------------


@pytest.mark.parametrize("archetype", ["cycle", "hub_spoke", "venn", "scene"])
@pytest.mark.parametrize("count", [3, 4, 5, 6])
@pytest.mark.parametrize("emphasis", [0, 1])
def test_absolutely_placed_plates_never_overlap(archetype, count, emphasis):
    boxes = _plate_boxes(archetype, count, emphasis)
    assert boxes, f"{archetype}/{count} placed no plate at all"
    for i, (name_a, (ax, ay, aw, ah)) in enumerate(boxes):
        for name_b, (bx, by, bw, bh) in boxes[i + 1 :]:
            overlap_x = min(ax + aw, bx + bw) - max(ax, bx)
            overlap_y = min(ay + ah, by + bh) - max(ay, by)
            assert overlap_x <= 0 or overlap_y <= 0, (
                f"{archetype}/n{count}: .{name_a} overlaps .{name_b} by "
                f"{overlap_x:.0f}x{overlap_y:.0f}px"
            )


@pytest.mark.parametrize("archetype", ["cycle", "hub_spoke", "venn", "scene"])
@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_absolutely_placed_plates_stay_inside_the_figure(archetype, count):
    for name, (x, y, width, height) in _plate_boxes(archetype, count):
        assert x >= 0 and x + width <= CANVAS_W, f"{archetype}/n{count}: .{name} leaves the figure"
        assert y >= 0 and y + height <= CANVAS_H, f"{archetype}/n{count}: .{name} leaves the figure"


#: How each archetype that stacks or spreads its parts claims the height it
#: was given. A figure that centres a short band inside a tall canvas reads as
#: an unfinished page, so every one of these declarations is load-bearing;
#: the rendered proof is the ``empty-band`` probe of the F2-verify sweep.
FILL_DECLARATIONS = [
    (["ig-process", "ig-node"], "flex", "1 1 0"),
    (["ig-milestones", "ig-node"], "flex", "1 1 0"),
    (["ig-roadmap", "ig-node"], "flex", "1 1 0"),
    (["ig-swimlane", "ig-node"], "flex", "1 1 0"),
    (["ig-checklist", "ig-node"], "flex", "1 1 0"),
    (["ig-layers", "ig-node"], "flex", "1 1 0"),
    (["ig-funnel", "ig-node"], "flex", "1 1 0"),
    (["ig-pyramid", "ig-node"], "flex", "1 1 0"),
    (["ig-hierarchy", "ig-children", "ig-node"], "flex", "1 1 0"),
    (["ig-gauge", "ig-facts", "ig-node"], "flex", "1 1 0"),
    (["ig-kpi"], "align-content", "stretch"),
    (["ig-icon-list"], "align-content", "stretch"),
    (["ig-compare"], "align-items", "stretch"),
    (["ig-before-after"], "align-items", "stretch"),
    (["ig-compare", "ig-table"], "height", "100%"),
    (["ig-before-after", "ig-table"], "height", "100%"),
]


@pytest.mark.parametrize(("chain", "prop", "value"), FILL_DECLARATIONS)
def test_an_archetype_claims_the_height_it_was_given(chain, prop, value):
    rules = _rules(library_css(Brand(topic="스마트팩토리", primary="#1E5EFF", fonts="Pretendard")))
    classes = [frozenset({"ig", chain[0], "n4"})] + [frozenset({name}) for name in chain[1:]]
    assert _declarations(rules, classes).get(prop) == value


@pytest.mark.parametrize("count", [3, 4, 5, 6])
def test_a_roadmap_bar_ends_inside_its_track(count):
    rules = _rules(library_css(Brand(topic="스마트팩토리", primary="#1E5EFF", fonts="Pretendard")))
    root = frozenset({"ig", "ig-roadmap", f"n{max(3, min(6, count))}"})
    for index in range(count):
        offset, width = roadmap_bar(index, count)
        classes = frozenset({"ig-fill", f"o{offset}", f"w{width}"})
        declarations = _declarations(rules, [root, frozenset({"ig-track"}), classes])
        left = float(declarations["left"].rstrip("%"))
        span = float(declarations["width"].rstrip("%"))
        assert left + span <= 100, (
            f"roadmap n{count} phase {index + 1}: .o{offset}.w{width} ends at "
            f"{left + span:.0f}% of its track"
        )
