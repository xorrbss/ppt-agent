"""The in-page card fit fragment the authored renderer probe splices into itself.

Design: ``docs/authored-in-render-fit-design-20260906.md`` (sections 3, 4, 7).

The model writes a fixed pixel height on a card and fills it with flow copy
whose wrapped height it cannot measure, so copy prints outside the card that
owns it.  Out of the browser that defect costs a provider call and several
confirm renders; inside the page it is a handful of synchronous layouts.  This
module builds the JavaScript that performs those layouts: grow the card into
the slack below it first, then shrink its owned text until the ink sits inside
the card, never below the font floor the gate enforces.

Two invariants make the fragment safe to own:

* every constant is interpolated from the Python detector that defines it, so
  the fitter and the detectors it must agree with cannot drift; and
* every edit is a plain string append onto the element's original ``style``
  attribute, never a CSSOM property assignment, so the attribute the browser
  measured and the attribute the API later reconstructs are the same bytes.

The fragment is spliced into the probe's IIFE scope by an rf-string template,
so the text returned here carries plain single braces; escaping is the
caller's business.
"""

from __future__ import annotations

import json

from services.authored_vnext.capability_registry import AUTHORED_FONT_FLOOR_PX
from utils.authored_dom_footer_zone import (
    _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
    _PAGE_NUMBER_MAX_FONT_PX,
    _PAGE_NUMBER_MIN_TOP_FRACTION,
    NUMBERED_CONTENT_BOTTOM_PX,
    UNNUMBERED_CONTENT_BOTTOM_PX,
)
from utils.authored_dom_text_escape import (
    _PAINTED_TAGS,
    _TRANSPARENT,
    ESCAPE_TOLERANCE_PX,
)

#: Opt-in class on ``<html>``.  ``class`` is a global attribute in the renderer
#: markup policy, so the signal needs no allowlist change, and the model cannot
#: know the name, so no authored stylesheet can target it.
FIT_CLASS = "codex-fit-v1"

#: Report version carried in the metrics payload under ``fit``.
FIT_VERSION = "authored-in-render-fit/v1"

#: The fitter targets this many pixels *inside* the card's inner edge, not the
#: detector's tolerance: a slide that passes here still passes when a different
#: Chromium with a different Korean fallback font measures the exported deck.
FIT_MARGIN_PX = 6

#: Per-slide budgets.  Constants, not knobs (design section 8).
MAX_FIT_CARDS = 16
MAX_FIT_LAYOUTS = 160
FIT_TIME_BUDGET_FRACTION = 0.10

#: Fallback when the caller does not publish ``window.__codexFitTimeoutMs``.
DEFAULT_FIT_TIMEOUT_MS = 60000

#: Seven halvings give a 1 % resolution on the shrink factor.
FIT_BISECTION_STEPS = 7


def fit_constants() -> dict[str, object]:
    """Every value the fragment interpolates, keyed as the JavaScript reads it."""

    return {
        "version": FIT_VERSION,
        "fit_class": FIT_CLASS,
        "tolerance_px": ESCAPE_TOLERANCE_PX,
        "margin_px": FIT_MARGIN_PX,
        "numbered_content_bottom_px": NUMBERED_CONTENT_BOTTOM_PX,
        "unnumbered_content_bottom_px": UNNUMBERED_CONTENT_BOTTOM_PX,
        "font_floor_px": AUTHORED_FONT_FLOOR_PX,
        "max_cards": MAX_FIT_CARDS,
        "max_layouts": MAX_FIT_LAYOUTS,
        "time_budget_fraction": FIT_TIME_BUDGET_FRACTION,
        "default_timeout_ms": DEFAULT_FIT_TIMEOUT_MS,
        "bisection_steps": FIT_BISECTION_STEPS,
        "page_number_max_font_px": _PAGE_NUMBER_MAX_FONT_PX,
        "page_number_max_distance_px": _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
        "page_number_min_top_fraction": _PAGE_NUMBER_MIN_TOP_FRACTION,
        "transparent_backgrounds": sorted(_TRANSPARENT),
        "painted_tags": sorted(_PAINTED_TAGS),
    }


_CONSTANTS_MARKER = "__FIT_CONSTANTS__"

# A raw string: the fragment carries regular-expression escapes, and it is the
# caller's rf-string that doubles braces when it interpolates this text.
_FRAGMENT = r"""
const __codexFit = {
  C: __FIT_CONSTANTS__,
  _snapshots: new Map(),
  _applied: new Map(),
  _last: null,
  _layouts: 0,
  _deadline: 0,
  _partial: false,
  _px(value) { const parsed = parseFloat(value); return Number.isFinite(parsed) ? parsed : 0; },
  _cssPx(value) {
    const text = String(value === null || value === undefined ? '' : value).trim().toLowerCase();
    const parsed = Number(text.endsWith('px') ? text.slice(0, -2) : text);
    return Number.isFinite(parsed) ? parsed : 0;
  },
  _round(value, digits) { const scale = Math.pow(10, digits); return Math.round(value * scale) / scale; },
  _depth(node) { let depth = 0; for (let cur = node.parentElement; cur; cur = cur.parentElement) depth += 1; return depth; },
  _budgetLeft() { return this._layouts < this.C.max_layouts && Date.now() < this._deadline; },
  // The infographic library owns its own geometry, and SVG text is out of scope.
  _eligible(node) {
    if (node === document.body || node === document.documentElement) return false;
    if (node.closest('svg') || node.closest('.ig')) return false;
    return !Array.from(node.classList || []).some((name) => name === 'ig' || name.indexOf('ig-') === 0);
  },
  _snapshot(node) {
    let snap = this._snapshots.get(node);
    if (!snap) {
      const style = getComputedStyle(node);
      snap = {
        style: node.hasAttribute('style') ? node.getAttribute('style') : null,
        fontSize: this._px(style.fontSize),
        lineHeight: String(style.lineHeight || 'normal')
      };
      this._snapshots.set(node, snap);
    }
    return snap;
  },
  _entry(node) {
    let entry = this._applied.get(node);
    if (!entry) {
      entry = {qid: node.getAttribute('data-qid'), height: null, text: null, factor: 1};
      this._applied.set(node, entry);
    }
    return entry;
  },
  _declarations(entry) {
    const parts = [];
    if (entry && entry.height) parts.push(entry.height);
    if (entry && entry.text) parts.push(entry.text);
    return parts.join(';');
  },
  // The one way this fitter is allowed to write a style: append the declarations
  // onto the snapshot attribute string.  CSSOM assignment re-serialises the whole
  // attribute, which would make the measured document differ from the one the API
  // reconstructs from the report.
  _write(node) {
    const snap = this._snapshot(node);
    const declarations = this._declarations(this._applied.get(node));
    if (!declarations) {
      if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
      this._applied.delete(node);
      return;
    }
    const original = snap.style || '';
    node.setAttribute('style', (original ? original + ';' : '') + declarations);
  },
  _save(nodes) {
    return nodes.map((node) => {
      const entry = this._applied.get(node);
      return {node, entry: entry ? {qid: entry.qid, height: entry.height, text: entry.text, factor: entry.factor} : null};
    });
  },
  _restore(saved) {
    for (const item of saved) {
      if (item.entry) this._applied.set(item.node, item.entry); else this._applied.delete(item.node);
      this._write(item.node);
    }
  },
  _ownedTextNodes(node) {
    const result = [];
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!String(text.textContent || '').trim()) continue;
      if (!text.parentElement || text.parentElement.closest('[data-qid]') !== node) continue;
      result.push(text);
    }
    return result;
  },
  // Glyph ink, not line boxes: a line rect's bottom sits half the leading below
  // the last descender.  Same em-box rule as utils/authored_dom_text_escape.py.
  _ink(node) {
    const font = this._px(getComputedStyle(node).fontSize);
    let right = null, bottom = null;
    for (const text of this._ownedTextNodes(node)) {
      const range = document.createRange();
      range.selectNodeContents(text);
      for (const rect of range.getClientRects()) {
        if (rect.width <= 0 || rect.height <= 0) continue;
        right = right === null ? rect.right : Math.max(right, rect.right);
        const ink = rect.bottom - Math.max(0, (rect.height - font) / 2);
        bottom = bottom === null ? ink : Math.max(bottom, ink);
      }
    }
    return right === null ? null : {right: right, bottom: bottom};
  },
  _isCardLike(node, style) {
    if (this.C.painted_tags.indexOf(node.tagName.toLowerCase()) >= 0) return true;
    const background = String(style.backgroundColor || '').replace(/ /g, '').toLowerCase();
    if (background && this.C.transparent_backgrounds.indexOf(background) < 0) return true;
    const image = String(style.backgroundImage === undefined ? 'none' : style.backgroundImage).trim().toLowerCase();
    if (image && image !== 'none') return true;
    for (const axis of ['overflowY', 'overflowX', 'overflow']) {
      const mode = String(style[axis] || '').trim().toLowerCase();
      if (mode && mode !== 'visible') return true;
    }
    if (['TopLeft', 'TopRight', 'BottomLeft', 'BottomRight'].some((corner) => this._cssPx(style['border' + corner + 'Radius']) > 0)) return true;
    const shadow = String(style.boxShadow === undefined ? 'none' : style.boxShadow).trim().toLowerCase();
    if (shadow && shadow !== 'none') return true;
    return ['Top', 'Right', 'Bottom', 'Left'].every((side) => this._cssPx(style['border' + side + 'Width']) > 0);
  },
  _escape(card, leaves, margin) {
    this._layouts += 1;
    const style = getComputedStyle(card);
    const rect = card.getBoundingClientRect();
    const innerRight = rect.right - this._cssPx(style.borderRightWidth);
    const innerBottom = rect.bottom - this._cssPx(style.borderBottomWidth);
    let escape = 0;
    for (const leaf of leaves) {
      const ink = this._ink(leaf);
      if (!ink) continue;
      escape = Math.max(escape, ink.bottom - (innerBottom - margin), ink.right - (innerRight - margin));
    }
    return Math.max(0, escape);
  },
  // Growable only when the height is authored: an inline px height, or a matched
  // rule with a px height.  Anything the fragment cannot read is not grown.
  _explicitHeightPx(node) {
    const inline = node.style ? String(node.style.height || '').trim().toLowerCase() : '';
    if (inline) return inline.endsWith('px') ? this._cssPx(inline) : null;
    let declared = null, sheets = null;
    try { sheets = Array.from(document.styleSheets); } catch (_) { return null; }
    for (const sheet of sheets) {
      let rules = null;
      try { rules = sheet.cssRules; } catch (_) { continue; }
      if (!rules) continue;
      for (const rule of Array.from(rules)) {
        if (!rule || !rule.selectorText || !rule.style) continue;
        const value = String(rule.style.height || '').trim().toLowerCase();
        if (!value) continue;
        let matched = false;
        try { matched = node.matches(rule.selectorText); } catch (_) { continue; }
        if (!matched) continue;
        if (!value.endsWith('px')) return null;
        declared = this._cssPx(value);
      }
    }
    return declared;
  },
  _contentLine(qids) {
    const numbered = qids.some((node) => {
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      const font = this._px(getComputedStyle(node).fontSize);
      const text = String(node.innerText || node.textContent || '').trim();
      return rect.left <= 2 && rect.right >= innerWidth - 4 &&
        innerHeight - rect.bottom >= 0 && innerHeight - rect.bottom <= this.C.page_number_max_distance_px &&
        rect.top >= innerHeight * this.C.page_number_min_top_fraction &&
        font > 0 && font <= this.C.page_number_max_font_px && /^\d{1,3}$/.test(text);
    });
    return numbered ? this.C.numbered_content_bottom_px : this.C.unnumbered_content_bottom_px;
  },
  // A grown card must stay above the footer line, inside the first ancestor that
  // would clip it, and above the next element that starts below it.
  _slack(card, contentLine, qids) {
    this._layouts += 1;
    const rect = card.getBoundingClientRect();
    let limit = contentLine;
    for (let parent = card.parentElement; parent; parent = parent.parentElement) {
      if (parent === document.body || parent === document.documentElement) break;
      const style = getComputedStyle(parent);
      const clipping = ['overflowY', 'overflowX', 'overflow'].some((axis) => {
        const mode = String(style[axis] || '').trim().toLowerCase();
        return mode && mode !== 'visible';
      });
      if (!clipping && this._explicitHeightPx(parent) === null) continue;
      const parentRect = parent.getBoundingClientRect();
      limit = Math.min(limit, parentRect.bottom - this._cssPx(style.borderBottomWidth) - this._cssPx(style.paddingBottom));
      break;
    }
    for (const other of qids) {
      if (other === card || card.contains(other) || other.contains(card)) continue;
      const otherRect = other.getBoundingClientRect();
      if (otherRect.width <= 0 || otherRect.height <= 0 || otherRect.top < rect.bottom) continue;
      if (Math.min(rect.right, otherRect.right) <= Math.max(rect.left, otherRect.left)) continue;
      limit = Math.min(limit, otherRect.top);
    }
    return limit - rect.bottom;
  },
  _applyFactor(bases, factor) {
    for (const item of bases) {
      const scale = item.applied * factor;
      const parts = ['font-size:' + this._round(item.snapFont * scale, 1) + 'px'];
      const lineHeight = String(item.lineHeight || '').trim().toLowerCase();
      if (lineHeight.endsWith('px')) parts.push('line-height:' + this._round(this._cssPx(lineHeight) * scale, 1) + 'px');
      const entry = this._entry(item.leaf);
      entry.text = parts.join(';');
      entry.factor = scale;
      this._write(item.leaf);
    }
  },
  // Largest factor whose ink clears the margin, bisected over [floor, 1].  The
  // floor is the product floor: an inner card's factor is already in `applied`.
  _shrink(card, leaves) {
    const bases = [];
    for (const leaf of leaves) {
      const snap = this._snapshot(leaf);
      const entry = this._applied.get(leaf);
      const applied = entry && entry.factor ? entry.factor : 1;
      if (snap.fontSize * applied > 0) bases.push({leaf: leaf, snapFont: snap.fontSize, applied: applied, lineHeight: snap.lineHeight});
    }
    if (!bases.length) return null;
    const floor = Math.min(1, Math.max.apply(null, bases.map((item) => this.C.font_floor_px / (item.snapFont * item.applied))));
    if (!(floor < 1)) return null;
    this._applyFactor(bases, floor);
    if (this._escape(card, leaves, this.C.margin_px) > 0 && this._escape(card, leaves, 0) > this.C.tolerance_px) return null;
    let best = floor, low = floor, high = 1, last = floor;
    for (let step = 0; step < this.C.bisection_steps && this._budgetLeft(); step += 1) {
      const mid = (low + high) / 2;
      this._applyFactor(bases, mid);
      last = mid;
      if (this._escape(card, leaves, this.C.margin_px) <= 0) { best = mid; low = mid; } else high = mid;
    }
    if (last !== best) this._applyFactor(bases, best);
    return best;
  },
  _fitCard(card, contentLine, qids) {
    const leaves = Array.from(card.querySelectorAll('[data-qid]'))
      .filter((node) => this._eligible(node) && this._ownedTextNodes(node).length > 0);
    if (!leaves.length) return false;
    const startLayouts = this._layouts;
    let escape = this._escape(card, leaves, this.C.margin_px);
    if (escape <= 0) return false;
    const record = {
      qid: card.getAttribute('data-qid'), escape_px: this._round(this._escape(card, leaves, 0), 2),
      grow_px: 0, factor: 1, outcome: 'unfixable:floor', layouts: 0
    };
    this._last.cards.push(record);
    if (leaves.some((leaf) => ['absolute', 'fixed'].indexOf(String(getComputedStyle(leaf).position || '')) >= 0)) {
      record.outcome = 'unfixable:absolute';
      record.layouts = this._layouts - startLayouts;
      return true;
    }
    const saved = this._save([card].concat(leaves));
    let grown = 0;
    const declared = this._explicitHeightPx(card);
    if (declared !== null) {
      const grow = Math.min(escape, Math.max(0, this._slack(card, contentLine, qids)));
      if (grow > 0) {
        const style = getComputedStyle(card);
        const base = String(style.boxSizing || '') === 'border-box' ? card.getBoundingClientRect().height : declared;
        this._entry(card).height = 'height:' + this._round(base + grow, 1) + 'px';
        this._write(card);
        grown = grow;
        escape = this._escape(card, leaves, this.C.margin_px);
      }
    }
    record.grow_px = this._round(grown, 2);
    if (escape > 0) {
      const factor = this._shrink(card, leaves);
      if (factor === null) {
        this._restore(saved);
        record.grow_px = 0;
        record.layouts = this._layouts - startLayouts;
        return true;
      }
      record.factor = this._round(factor, 4);
      record.outcome = grown > 0 ? 'grown_and_shrunk' : 'shrunk';
    } else record.outcome = 'grown';
    record.layouts = this._layouts - startLayouts;
    return true;
  },
  _run() {
    const qids = Array.from(document.querySelectorAll('[data-qid]')).filter((node) => this._eligible(node));
    // One snapshot pass before any edit, so no factor is ever compounded onto a
    // value another card's fit already changed.  Unmutated style reads share a
    // single recalculation, so the pass costs one layout, not one per element.
    for (const node of qids) this._snapshot(node);
    this._layouts += 1;
    const contentLine = this._contentLine(qids);
    const cards = qids
      .filter((node) => this._isCardLike(node, getComputedStyle(node)))
      .map((node) => ({node: node, depth: this._depth(node)}))
      .sort((left, right) => right.depth - left.depth)
      .map((item) => item.node);
    this._layouts += 1;
    let fitted = 0;
    for (const card of cards) {
      if (fitted >= this.C.max_cards || !this._budgetLeft()) {
        this._partial = true;
        this._last.cards.push({qid: card.getAttribute('data-qid'), escape_px: null, grow_px: 0, factor: 1, outcome: 'skipped:budget', layouts: 0});
        continue;
      }
      if (this._fitCard(card, contentLine, qids)) fitted += 1;
    }
  },
  _editList() {
    const edits = [];
    for (const [node, entry] of this._applied) {
      const declarations = this._declarations(entry);
      if (declarations) edits.push({qid: entry.qid || node.getAttribute('data-qid'), declarations: declarations});
    }
    return edits;
  },
  reset() {
    for (const [node, snap] of this._snapshots) {
      try {
        if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
      } catch (_) {}
    }
    this._snapshots = new Map();
    this._applied = new Map();
    this._layouts = 0;
    this._partial = false;
  },
  apply(pass) {
    this.reset();
    const requested = document.documentElement.classList.contains(this.C.fit_class);
    this._last = {
      version: this.C.version, requested: requested, fit_state: String(pass || 'provisional'),
      partial: false, error: null, cards: [], edits: []
    };
    if (!requested) return this._last;
    const timeout = Number(window.__codexFitTimeoutMs);
    const budget = (Number.isFinite(timeout) && timeout > 0 ? timeout : this.C.default_timeout_ms) * this.C.time_budget_fraction;
    this._deadline = Date.now() + budget;
    // A fitter defect must never cost the slide its metrics: unavailable metrics
    // block publication, an unfitted measurement does not.
    try {
      this._run();
      this._last.partial = this._partial;
      this._last.edits = this._editList();
    } catch (error) {
      this.reset();
      this._last.edits = [];
      this._last.partial = true;
      const name = String((error && error.name) || 'Error');
      this._last.error = name + ': ' + String((error && error.message) || error || '').slice(0, 200);
    }
    return this._last;
  },
  report() { return this._last; }
};
"""


def fit_script_fragment() -> str:
    """The fragment the probe splices into its IIFE scope, constants interpolated."""

    constants = json.dumps(fit_constants(), sort_keys=True, indent=2)
    return _FRAGMENT.replace(_CONSTANTS_MARKER, constants)


__all__ = [
    "DEFAULT_FIT_TIMEOUT_MS",
    "FIT_BISECTION_STEPS",
    "FIT_CLASS",
    "FIT_MARGIN_PX",
    "FIT_TIME_BUDGET_FRACTION",
    "FIT_VERSION",
    "MAX_FIT_CARDS",
    "MAX_FIT_LAYOUTS",
    "fit_constants",
    "fit_script_fragment",
]
