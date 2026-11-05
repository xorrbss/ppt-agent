"""Measured browser space advance for system-composed header text.

Header preflight predicts line widths from the bundled fonts' own shaping
(``services.svg_text_advance``: HarfBuzz with no language). Chromium can draw a
space wider than that: Noto Sans KR's Korean ``locl`` replaces the 220-unit
space with a 280-unit one, and whether Chromium shapes a space as Korean
depends on its neighbours and on the host. On an en-US Linux host only a space
between two Hangul syllables widens (1.76 px at 34 px); a space next to a Latin
letter, a digit or punctuation does not. The sealed ``CHROMIUM_SPACE_ADVANCE_EM``
(measured as "A A" on Windows) widened a space between Latin letters as well.
A fixed constant therefore suits one host and misplaces the other.

Header measurement ``svg-common-header-glyph/v3`` (v1 contracts) and
``svg-common-header-glyph-paint/v4`` (v2 contracts) instead measure the space
deviation on the renderer that draws the header, per face and per neighbour
pair (``hangul`` syllable or ``other`` on each side), and seal it with that
renderer's identity into the contract. Validation reads the sealed values and
never re-measures, so a stored contract validates byte-for-byte on any host,
and another platform or browser produces a different contract instead of
silently reusing another renderer's number.

For each face and pair the probe renders ``L RL RL RL R`` and ``LRLRLRLR``
through ``render_svg_with_metrics`` (the same admission, font embedding and
renderer as a page). The space deviation is

    ((browser(spaced) - font(spaced)) - (browser(solid) - font(solid))) / spaces

in em, which cancels any common browser/font offset of the glyphs. The probe
also checks the assumptions the width model rests on instead of taking them for
granted: the renderer follows the font for the probe glyphs, the deviation
scales linearly with font size (a reference and a small size), and the
``other`` class behaves as one class (a Latin letter and a digit agree). A face
that fails a check has no measurement.
"""

from __future__ import annotations

import asyncio
import logging
import math
import platform
import sys

from utils.hashing import canonical_json_digest

logger = logging.getLogger(__name__)

PROBE_VERSION = "svg-common-header-space-probe/v1"
#: Every face the system header can draw: Noto Sans KR titles default to 800.
PROBE_FACES = (("Noto Sans KR", 400), ("Noto Sans KR", 700), ("Noto Sans KR", 800),
               ("Pretendard", 400), ("Pretendard", 700))
#: Neighbour classes of a space, with the glyphs that represent each class.
_CLASS_GLYPHS = {"hangul": ("가",), "other": ("H", "7")}
SPACE_CONTEXTS = tuple(f"{left}|{right}" for left in _CLASS_GLYPHS for right in _CLASS_GLYPHS)
_PROBE_SPACES = 4
_REFERENCE_SIZE_PX = 64
_SCALE_CHECK_SIZE_PX = 24
#: Browser widths are LayoutUnits (1/64 px). Each bound is far above that
#: quantization at the probe sizes and far below what would move a header line
#: by a pixel: 0.001 em is 0.04 px per glyph or space at a 40 px title.
_GLYPH_CONFORMANCE_EM = 0.001
_AGREEMENT_EM = 0.001
_PROBE_WIDTH_PX = 1280
_SOLID_COLUMN_X = 660
_ENTRY_KEYS = frozenset({"family", "weight", "digest", "contexts"})
_SAMPLE_KEYS = frozenset({"pair", "space_delta_em", "scale_check_delta_em", "glyph_offset_em"})

#: renderer identity digest -> {"renderer": identity, "faces": {(family, weight, digest): entry}}
_measurements: dict[str, dict] = {}
#: renderer identity digest -> why the last probe produced no measurement
_failures: dict[str, str] = {}


def _space_class(char: str) -> str:
    return "hangul" if "가" <= char <= "힣" else "other"


def space_contexts(text: str) -> list[str]:
    """The neighbour pair of every space in ``text``, which has no outer or repeated spaces."""
    return [f"{_space_class(text[index - 1])}|{_space_class(text[index + 1])}"
            for index, char in enumerate(text) if char == " "]


def renderer_identity() -> dict:
    """The identity of the renderer that draws header text in this process.

    In-process and owned-local renders run the resolved Chrome binary, named by
    its path, size, mtime and ``--version`` (``chrome_runtime_digest``). An
    external worker's browser is not observable here, so its launcher command
    names it instead. Host content-language effects are not part of the
    identity; they are captured by measuring on that renderer.
    """
    from services.svg_renderer import SVG_RENDERER_VERSION
    from utils.renderer_worker_policy import renderer_requires_external_worker, worker_command

    identity = {"renderer_version": SVG_RENDERER_VERSION}
    if renderer_requires_external_worker():
        identity["external_worker_command_digest"] = canonical_json_digest(worker_command())
    else:
        from utils.slide_capture import chrome_runtime_digest

        identity.update(platform=sys.platform, machine=platform.machine(),
                        chrome_runtime_digest=chrome_runtime_digest())
    identity["digest"] = canonical_json_digest(identity)
    return identity


def _face_digest(family: str, weight: int) -> str:
    from services.svg_common_header import _font

    return _font(family, weight, 1)["digest"]


def _pairs(context: str) -> list[str]:
    left, right = context.split("|")
    # The other class is probed by each representative on the other side of a
    # hangul glyph, and by each representative facing itself.
    if left == right == "other":
        return [glyph + glyph for glyph in _CLASS_GLYPHS["other"]]
    return [a + b for a in _CLASS_GLYPHS[left] for b in _CLASS_GLYPHS[right]]


def _probe_texts(pair: str) -> tuple[str, str]:
    left, right = pair
    return " ".join([left] + [right + left] * (_PROBE_SPACES - 1) + [right]), (left + right) * _PROBE_SPACES


def _probe_svg(family: str, weight: int) -> tuple[str, list[tuple[int, str]], int]:
    rows, nodes, y = [], [], 0.0
    for context in SPACE_CONTEXTS:
        for pair in _pairs(context):
            for size in (_REFERENCE_SIZE_PX, _SCALE_CHECK_SIZE_PX):
                y += size * 1.4
                for x, text in zip((8, _SOLID_COLUMN_X), _probe_texts(pair), strict=True):
                    rows.append((size, text))
                    nodes.append(f'<text x="{x}" y="{y:g}" font-family="{family}" font-size="{size}" '
                                 f'font-weight="{weight}" fill="#000000">{text}</text>')
    height = int(math.ceil(y + _REFERENCE_SIZE_PX))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_PROBE_WIDTH_PX}" height="{height}" '
           f'viewBox="0 0 {_PROBE_WIDTH_PX} {height}">' + "".join(nodes) + "</svg>")
    return svg, rows, height


def _face_from_metrics(family: str, weight: int, quality: dict, rows) -> tuple[dict | None, str | None]:
    from services.svg_text_advance import text_advance

    if not quality.get("measurement_complete"):
        raise ValueError("svg_common_header_space_probe_incomplete")
    metrics = (quality.get("browser_metrics") or {}).get("elements") or []
    by_qid = {item.get("qid"): item for item in metrics if item.get("has_owned_text")}
    offsets = {}
    # The root is qid svg-0; the probe texts follow in document order.
    for index, (size, text) in enumerate(rows, start=1):
        item = by_qid.get(f"svg-{index}")
        fragments = (item or {}).get("text_fragments") or []
        if (item is None or item.get("font_loaded") is not True or item.get("owned_text") != text
                or len(fragments) != 1):
            raise ValueError("svg_common_header_space_probe_incomplete")
        font_width = text_advance(text, size, str(weight), family)
        if font_width is None:
            raise ValueError("svg_common_header_space_probe_incomplete")
        offsets[(size, text)] = float(fragments[0]["width"]) - font_width
    contexts = {}
    for context in SPACE_CONTEXTS:
        samples = []
        for pair in _pairs(context):
            spaced, solid = _probe_texts(pair)
            deltas, glyph_offsets = [], []
            for size in (_REFERENCE_SIZE_PX, _SCALE_CHECK_SIZE_PX):
                deltas.append((offsets[(size, spaced)] - offsets[(size, solid)]) / (_PROBE_SPACES * size))
                glyph_offsets.append(offsets[(size, solid)] / (2 * _PROBE_SPACES * size))
            if any(abs(offset) > _GLYPH_CONFORMANCE_EM for offset in glyph_offsets):
                return None, f"svg_common_header_space_probe_glyph_nonconforming:{pair}"
            if abs(deltas[0] - deltas[1]) > _AGREEMENT_EM:
                return None, f"svg_common_header_space_probe_nonlinear:{pair}"
            samples.append({"pair": pair, "space_delta_em": deltas[0], "scale_check_delta_em": deltas[1],
                            "glyph_offset_em": glyph_offsets[0]})
        if max(s["space_delta_em"] for s in samples) - min(s["space_delta_em"] for s in samples) > _AGREEMENT_EM:
            return None, f"svg_common_header_space_probe_class_inconsistent:{context}"
        contexts[context] = samples
    return {"family": family, "weight": weight, "digest": _face_digest(family, weight),
            "contexts": contexts}, None


async def ensure_space_advance_measurement() -> None:
    """Measure this process's renderer once; later calls reuse the result.

    A failed probe records its cause and is retried on the next call. It never
    raises: only a header that needs the measurement fails, with that cause.
    """
    try:
        identity = renderer_identity()
    except Exception as error:  # renderer policy or binary resolution
        logger.warning("svg common header renderer identity unavailable: %s", error)
        return
    key = identity["digest"]
    if key in _measurements:
        return
    from services.svg_renderer import render_svg_with_metrics

    faces = {}
    try:
        for family, weight in PROBE_FACES:
            svg, rows, height = _probe_svg(family, weight)
            rendered = await render_svg_with_metrics(svg, width=_PROBE_WIDTH_PX, height=height, timeout=90)
            entry, rejected = _face_from_metrics(family, weight, rendered.quality, rows)
            if entry is None:
                logger.warning("svg common header space probe rejected %s %s: %s", family, weight, rejected)
                continue
            faces[(family, weight, entry["digest"])] = entry
    except Exception as error:
        _failures[key] = str(error) if isinstance(error, ValueError) else type(error).__name__
        logger.warning("svg common header space probe failed: %s", _failures[key])
        return
    _measurements[key] = {"renderer": identity, "faces": faces}
    _failures.pop(key, None)


def _current_measurement() -> dict:
    identity = renderer_identity()
    measurement = _measurements.get(identity["digest"])
    if measurement is not None:
        return measurement
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # A synchronous caller (a script or a test) can run the probe itself.
        # An async caller must await ensure_space_advance_measurement() first:
        # blocking its loop on a render could deadlock the render semaphore.
        asyncio.run(ensure_space_advance_measurement())
        measurement = _measurements.get(identity["digest"])
        if measurement is not None:
            return measurement
        raise ValueError("svg_common_header_space_advance_unmeasurable") from None
    if identity["digest"] in _failures:
        raise ValueError("svg_common_header_space_advance_unmeasurable")
    raise ValueError("svg_common_header_space_advance_unmeasured")


def face_measured(family: str, weight: int) -> bool:
    """Whether this renderer has a space measurement for the bundled face."""
    try:
        digest = _face_digest(family, weight)
    except ValueError:
        return False
    return (family, weight, digest) in _current_measurement()["faces"]


def space_advance_evidence(fonts) -> dict:
    """The sealed measurement for ``fonts`` on this process's renderer."""
    measurement = _current_measurement()
    entries = {}
    for font in fonts:
        key = (font.get("family"), font.get("weight"), font.get("digest"))
        entry = measurement["faces"].get(key)
        if entry is None:
            raise ValueError("svg_common_header_font_material_invalid")
        entries[key] = {**entry, "contexts": {name: [dict(sample) for sample in samples]
                                              for name, samples in entry["contexts"].items()}}
    return {"version": PROBE_VERSION, "renderer": dict(measurement["renderer"]),
            "faces": [entries[key] for key in sorted(entries)]}


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _valid_samples(context: str, samples) -> bool:
    if (not isinstance(samples, list) or [s.get("pair") if isinstance(s, dict) else None for s in samples]
            != _pairs(context)):
        return False
    for sample in samples:
        if (set(sample) != _SAMPLE_KEYS
                or not all(_finite(sample[name]) for name in _SAMPLE_KEYS - {"pair"})
                or abs(sample["space_delta_em"] - sample["scale_check_delta_em"]) > _AGREEMENT_EM
                or abs(sample["glyph_offset_em"]) > _GLYPH_CONFORMANCE_EM):
            return False
    deltas = [sample["space_delta_em"] for sample in samples]
    return max(deltas) - min(deltas) <= _AGREEMENT_EM


def validate_space_advance_evidence(evidence, fonts) -> None:
    """Fail closed for a malformed sealed measurement or one missing a used face."""
    if (not isinstance(evidence, dict) or set(evidence) != {"version", "renderer", "faces"}
            or evidence["version"] != PROBE_VERSION
            or not isinstance(evidence["renderer"], dict)
            or evidence["renderer"].get("digest") != canonical_json_digest(
                {k: v for k, v in evidence["renderer"].items() if k != "digest"})
            or not isinstance(evidence["faces"], list)):
        raise ValueError("svg_common_header_digest_mismatch")
    keys = set()
    for entry in evidence["faces"]:
        contexts = entry.get("contexts") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS
                or not isinstance(entry["family"], str) or not isinstance(entry["digest"], str)
                or type(entry["weight"]) is not int
                or not isinstance(contexts, dict) or set(contexts) != set(SPACE_CONTEXTS)
                or not all(_valid_samples(name, samples) for name, samples in contexts.items())):
            raise ValueError("svg_common_header_digest_mismatch")
        key = (entry["family"], entry["weight"], entry["digest"])
        if key in keys:
            raise ValueError("svg_common_header_digest_mismatch")
        keys.add(key)
    for font in fonts:
        if (font.get("family"), font.get("weight"), font.get("digest")) not in keys:
            raise ValueError("svg_common_header_font_material_invalid")


def space_deltas_em(font, evidence) -> tuple[float, ...]:
    """The sealed space deviation of ``font`` per ``SPACE_CONTEXTS`` entry.

    Each context uses its first representative pair; the others only prove
    that the class behaves as one.
    """
    key = (font.get("family"), font.get("weight"), font.get("digest"))
    for entry in (evidence or {}).get("faces", ()):
        if (entry["family"], entry["weight"], entry["digest"]) == key:
            return tuple(entry["contexts"][name][0]["space_delta_em"] for name in SPACE_CONTEXTS)
    raise ValueError("svg_common_header_font_material_invalid")


def space_deviation_em(text: str, deltas: tuple[float, ...]) -> float:
    """The browser's extra width of every space in ``text``, in em."""
    by_context = dict(zip(SPACE_CONTEXTS, deltas, strict=True))
    return sum(by_context[context] for context in space_contexts(text))


__all__ = [
    "PROBE_FACES", "PROBE_VERSION", "SPACE_CONTEXTS", "ensure_space_advance_measurement", "face_measured",
    "renderer_identity", "space_advance_evidence", "space_contexts", "space_deltas_em",
    "space_deviation_em", "validate_space_advance_evidence",
]
