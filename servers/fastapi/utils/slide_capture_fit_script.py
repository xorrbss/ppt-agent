"""The in-page card fit fragment the authored renderer probe splices into itself.

Design: ``docs/authored-in-render-fit-design-20260906.md`` (sections 3, 4, 7).  A card carries an authored
pixel height and copy whose wrapped height the model cannot measure, so the copy prints outside it; in the
page the fix is a few synchronous layouts.  Every constant is interpolated from the Python detector that
defines it, so fitter and detectors cannot drift, and every edit is a plain string append onto the element's
own ``style`` attribute, so the attribute the browser measured is the one the API rebuilds.  The brace
escaping is the caller's rf-string; this text is plain JavaScript.
"""

from __future__ import annotations

import json

from services.authored_vnext.capability_registry import AUTHORED_FONT_FLOOR_PX
from utils.authored_dom_footer_zone import (  # the deck's footer safe lines
    _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX, _PAGE_NUMBER_MAX_FONT_PX,
    _PAGE_NUMBER_MIN_TOP_FRACTION, NUMBERED_CONTENT_BOTTOM_PX, UNNUMBERED_CONTENT_BOTTOM_PX)
from utils.authored_dom_text_escape import (  # the card predicate and its tolerance
    _PAINTED_TAGS, _TRANSPARENT, ESCAPE_TOLERANCE_PX)

#: Opt-in class on ``<html>``: a global attribute needs no markup-allowlist change, and
#: the model cannot know the name, so no authored stylesheet can target it.
FIT_CLASS = "codex-fit-v1"
FIT_VERSION = "authored-in-render-fit/v1"
#: The fit targets this many pixels *inside* the card's inner edge, not the detector's
#: tolerance, so it survives another Chromium with another Korean fallback font.
FIT_MARGIN_PX = 6
#: Per-slide budgets and shrink resolution.  Constants, not knobs.
MAX_FIT_CARDS = 16
MAX_FIT_LAYOUTS = 160
FIT_TIME_BUDGET_FRACTION = 0.10
DEFAULT_FIT_TIMEOUT_MS = 60000
FIT_BISECTION_STEPS = 7
_CONSTANTS_MARKER = "__FIT_CONSTANTS__"


def fit_constants() -> dict[str, object]:
    """Every value the fragment interpolates, keyed as the JavaScript reads it."""

    return {
        "version": FIT_VERSION, "fit_class": FIT_CLASS, "margin_px": FIT_MARGIN_PX,
        "tolerance_px": ESCAPE_TOLERANCE_PX, "font_floor_px": AUTHORED_FONT_FLOOR_PX,
        "numbered_content_bottom_px": NUMBERED_CONTENT_BOTTOM_PX, "max_cards": MAX_FIT_CARDS,
        "unnumbered_content_bottom_px": UNNUMBERED_CONTENT_BOTTOM_PX, "max_layouts": MAX_FIT_LAYOUTS,
        "time_budget_fraction": FIT_TIME_BUDGET_FRACTION, "bisection_steps": FIT_BISECTION_STEPS,
        "default_timeout_ms": DEFAULT_FIT_TIMEOUT_MS, "page_number_max_font_px": _PAGE_NUMBER_MAX_FONT_PX,
        "page_number_max_distance_px": _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
        "page_number_min_top_fraction": _PAGE_NUMBER_MIN_TOP_FRACTION,
        "transparent_backgrounds": sorted(_TRANSPARENT), "painted_tags": sorted(_PAINTED_TAGS),
    }


# Raw: the fragment carries a regular-expression escape.
_FRAGMENT = r"""
const __codexFit = {
  C: __FIT_CONSTANTS__,
  _snapshots: new Map(), _applied: new Map(), _last: null, _layouts: 0, _deadline: 0, _partial: false,
  _px(value) { const parsed = parseFloat(value); return Number.isFinite(parsed) ? parsed : 0; },
  _cssPx(value) {
    const text = String(value === null || value === undefined ? '' : value).trim().toLowerCase();
    const parsed = Number(text.endsWith('px') ? text.slice(0, -2) : text);
    return Number.isFinite(parsed) ? parsed : 0;
  },
  _round(value, digits) { const scale = Math.pow(10, digits); return Math.round(value * scale) / scale; },
  _depth(node) { let d = 0; for (let up = node.parentElement; up; up = up.parentElement) d += 1; return d; },
  _budgetLeft() { return this._layouts < this.C.max_layouts && Date.now() < this._deadline; },
  // The infographic library owns its own geometry; SVG text is out of scope.
  _eligible(node) {
    if (node === document.body || node === document.documentElement) return false;
    if (node.closest('svg') || node.closest('.ig')) return false;
    return !Array.from(node.classList || []).some((name) => name === 'ig' || name.indexOf('ig-') === 0);
  },
  _snapshot(node) {
    if (this._snapshots.has(node)) return this._snapshots.get(node);
    const style = getComputedStyle(node);
    this._snapshots.set(node, {style: node.hasAttribute('style') ? node.getAttribute('style') : null,
      fontSize: this._px(style.fontSize), lineHeight: String(style.lineHeight || 'normal')});
    return this._snapshots.get(node);
  },
  _entry(node) {
    if (!this._applied.has(node)) this._applied.set(node,
      {qid: node.getAttribute('data-qid'), height: null, text: null, factor: 1});
    return this._applied.get(node);
  },
  _declarations(entry) { return [entry && entry.height, entry && entry.text].filter(Boolean).join(';'); },
  // The one write this fitter is allowed: append onto the snapshot attribute string.  CSSOM
  // assignment re-serialises it, so the DOM would stop being the document the API rebuilds.
  _write(node) {
    const snap = this._snapshot(node), declarations = this._declarations(this._applied.get(node));
    if (declarations) { node.setAttribute('style', (snap.style ? snap.style + ';' : '') + declarations); return; }
    if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
    this._applied.delete(node);
  },
  _save(nodes) {
    return nodes.map((node) => ({node: node,
      entry: this._applied.has(node) ? Object.assign({}, this._applied.get(node)) : null}));
  },
  _restore(saved) {
    for (const item of saved) {
      if (item.entry) this._applied.set(item.node, item.entry); else this._applied.delete(item.node);
      this._write(item.node);
    }
  },
  _ownedTextNodes(node) {
    const result = [], walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!String(text.textContent || '').trim() || !text.parentElement) continue;
      if (text.parentElement.closest('[data-qid]') === node) result.push(text);
    }
    return result;
  },
  // Glyph ink, not line boxes (utils/authored_dom_text_escape.py, _ink_extent).
  _ink(node) {
    const font = this._px(getComputedStyle(node).fontSize), rects = [];
    for (const text of this._ownedTextNodes(node)) {
      const range = document.createRange(); range.selectNodeContents(text);
      for (const rect of range.getClientRects()) if (rect.width > 0 && rect.height > 0) rects.push(rect);
    }
    if (!rects.length) return null;
    return {right: Math.max.apply(null, rects.map((rect) => rect.right)),
      bottom: Math.max.apply(null, rects.map((rect) => rect.bottom - Math.max(0, (rect.height - font) / 2)))};
  },
  // Mirrors _is_card_like: painted tag, background, image, clipping, radius, shadow, borders.
  _isCardLike(node, style) {
    const text = (name, fb) => String(style[name] === undefined ? fb : style[name]).trim().toLowerCase();
    const set = (name) => text(name, 'none') !== '' && text(name, 'none') !== 'none';
    const background = String(style.backgroundColor || '').replace(/ /g, '').toLowerCase();
    return this.C.painted_tags.indexOf(node.tagName.toLowerCase()) >= 0 ||
      Boolean(background && this.C.transparent_backgrounds.indexOf(background) < 0) ||
      set('backgroundImage') || set('boxShadow') ||
      ['overflowY', 'overflowX', 'overflow'].some((a) => text(a, '') !== '' && text(a, '') !== 'visible') ||
      ['TopLeft', 'TopRight', 'BottomLeft', 'BottomRight'].some(
        (corner) => this._cssPx(style['border' + corner + 'Radius']) > 0) ||
      ['Top', 'Right', 'Bottom', 'Left'].every((side) => this._cssPx(style['border' + side + 'Width']) > 0);
  },
  // A footerLine mirrors reconcile_footer_zone_reports: with the card itself below that line, a
  // leaf whose ink also passes it is reported as a footer intrusion, not as an escape from here.
  _escape(card, leaves, margin, footerLine) {
    this._layouts += 1;
    const style = getComputedStyle(card), rect = card.getBoundingClientRect();
    const right = rect.right - this._cssPx(style.borderRightWidth) - margin;
    const bottom = rect.bottom - this._cssPx(style.borderBottomWidth) - margin;
    const bound = footerLine > 0 && rect.bottom > footerLine ? footerLine : Infinity;
    return leaves.map((leaf) => this._ink(leaf)).filter((ink) => ink && ink.bottom <= bound).reduce(
      (escape, ink) => Math.max(escape, ink.bottom - bottom, ink.right - right), 0);
  },
  // Growable only when the height is authored: an inline or matched px height.
  _explicitHeightPx(node) {
    const inline = node.style ? String(node.style.height || '').trim().toLowerCase() : '';
    if (inline) return inline.endsWith('px') ? this._cssPx(inline) : null;
    let declared = null, sheets = null;
    try { sheets = Array.from(document.styleSheets); } catch (_) { return null; }
    for (const sheet of sheets) {
      let rules = null;
      try { rules = sheet.cssRules; } catch (_) { continue; }
      for (const rule of Array.from(rules || [])) {
        const value = rule && rule.style ? String(rule.style.height || '').trim().toLowerCase() : '';
        let matched = false;
        try { matched = value ? node.matches(rule.selectorText) : false; } catch (_) { continue; }
        if (!matched) continue;
        if (!value.endsWith('px')) return null; else declared = this._cssPx(value);
      }
    }
    return declared;
  },
  _contentLine(qids) {
    const numbered = qids.some((node) => {
      const rect = node.getBoundingClientRect(), font = this._px(getComputedStyle(node).fontSize);
      return rect.width > 0 && rect.height > 0 && rect.left <= 2 && rect.right >= innerWidth - 4 &&
        font > 0 && font <= this.C.page_number_max_font_px && innerHeight - rect.bottom >= 0 &&
        innerHeight - rect.bottom <= this.C.page_number_max_distance_px &&
        rect.top >= innerHeight * this.C.page_number_min_top_fraction &&
        /^\d{1,3}$/.test(String(node.innerText || node.textContent || '').trim());
    });
    return numbered ? this.C.numbered_content_bottom_px : this.C.unnumbered_content_bottom_px;
  },
  // A grown card stays above the footer line, inside the first ancestor that would clip it.
  _slack(card, contentLine, qids) {
    this._layouts += 1;
    const rect = card.getBoundingClientRect();
    let limit = contentLine;
    for (let up = card.parentElement; up && up !== document.body; up = up.parentElement) {
      const style = getComputedStyle(up);
      const clipping = ['overflowY', 'overflowX', 'overflow'].some((axis) => {
        const mode = String(style[axis] || '').trim().toLowerCase();
        return mode && mode !== 'visible';
      });
      if (!clipping && this._explicitHeightPx(up) === null) continue;
      const box = up.getBoundingClientRect();
      limit = Math.min(limit, box.bottom - this._cssPx(style.borderBottomWidth) - this._cssPx(style.paddingBottom));
      break;
    }
    for (const other of qids) {
      if (other === card || card.contains(other) || other.contains(card)) continue;
      const box = other.getBoundingClientRect();
      if (box.width <= 0 || box.height <= 0 || box.top < rect.bottom) continue;
      if (Math.min(rect.right, box.right) > Math.max(rect.left, box.left)) limit = Math.min(limit, box.top);
    }
    return limit - rect.bottom;
  },
  _applyFactor(bases, factor) {
    for (const item of bases) {
      const scale = item.applied * factor, entry = this._entry(item.leaf);
      const parts = ['font-size:' + this._round(item.font * scale, 1) + 'px'];
      const lh = String(item.lh || '').trim().toLowerCase();
      if (lh.endsWith('px')) parts.push('line-height:' + this._round(this._cssPx(lh) * scale, 1) + 'px');
      entry.text = parts.join(';'); entry.factor = scale;
      this._write(item.leaf);
    }
  },
  // Largest factor clearing the margin, bisected over [floor, 1].  The floor is the product
  // floor: an inner card's own factor is already in `applied`.
  _shrink(card, leaves) {
    const bases = [];
    for (const leaf of leaves) {
      const snap = this._snapshot(leaf), entry = this._applied.get(leaf);
      const at = entry && entry.factor ? entry.factor : 1;
      if (snap.fontSize * at > 0) bases.push({leaf: leaf, font: snap.fontSize, applied: at, lh: snap.lineHeight});
    }
    if (!bases.length) return null;
    const floor = Math.max.apply(null, bases.map((item) => this.C.font_floor_px / (item.font * item.applied)));
    if (!(floor < 1)) return null;
    this._applyFactor(bases, floor);
    // The tolerance, not the margin, is the floor of acceptance for calling a card unfixable.
    if (this._escape(card, leaves, this.C.margin_px) > 0 &&
        this._escape(card, leaves, 0) > this.C.tolerance_px) return null;
    let best = floor, low = floor, high = 1, last = floor;
    for (let step = 0; step < this.C.bisection_steps && this._budgetLeft(); step += 1) {
      const mid = (low + high) / 2;
      this._applyFactor(bases, mid); last = mid;
      if (this._escape(card, leaves, this.C.margin_px) <= 0) { best = mid; low = mid; } else high = mid;
    }
    if (last !== best) this._applyFactor(bases, best);
    return best;
  },
  _fitCard(card, contentLine, qids) {
    const leaves = Array.from(card.querySelectorAll('[data-qid]')).filter(
      (node) => this._eligible(node) && this._ownedTextNodes(node).length > 0);
    if (!leaves.length) return false;
    const start = this._layouts;
    // Entry is the detector's question, the target is the fitter's: engage only a card the
    // detector would flag -- ink more than the tolerance past the inner edge, not reconciled
    // away as a footer intrusion.  The price: a card escaping by 1px keeps no export headroom.
    const escapePx = this._escape(card, leaves, 0, contentLine);
    if (escapePx <= this.C.tolerance_px) return false;
    let escape = this._escape(card, leaves, this.C.margin_px);
    const record = {qid: card.getAttribute('data-qid'), escape_px: this._round(escapePx, 2),
      grow_px: 0, factor: 1, outcome: 'unfixable:floor', layouts: 0};
    this._last.cards.push(record);
    const done = (o) => { if (o) record.outcome = o; record.layouts = this._layouts - start; return true; };
    if (leaves.some((leaf) => ['absolute', 'fixed'].indexOf(
      String(getComputedStyle(leaf).position || '')) >= 0)) return done('unfixable:absolute');
    const saved = this._save([card].concat(leaves));
    const declared = this._explicitHeightPx(card);
    let grown = 0;
    if (declared !== null) {
      const grow = Math.min(escape, Math.max(0, this._slack(card, contentLine, qids)));
      if (grow > 0) {
        const style = getComputedStyle(card);
        const base = style.boxSizing === 'border-box' ? card.getBoundingClientRect().height : declared;
        this._entry(card).height = 'height:' + this._round(base + grow, 1) + 'px';
        this._write(card); grown = grow; escape = this._escape(card, leaves, this.C.margin_px);
      }
    }
    record.grow_px = this._round(grown, 2);
    if (escape <= 0) return done('grown');
    const factor = this._shrink(card, leaves);
    if (factor === null) { this._restore(saved); record.grow_px = 0; return done(null); }
    record.factor = this._round(factor, 4);
    return done(grown > 0 ? 'grown_and_shrunk' : 'shrunk');
  },
  _run() {
    const qids = Array.from(document.querySelectorAll('[data-qid]')).filter((node) => this._eligible(node));
    // One snapshot pass before any edit, so no factor is compounded onto a value another card's
    // fit changed; unmutated reads share one recalculation, so the pass costs one layout.
    for (const node of qids) this._snapshot(node);
    this._layouts += 1;
    const contentLine = this._contentLine(qids);
    // Innermost first: an inner card is settled before its container is measured.
    const cards = qids.filter((node) => this._isCardLike(node, getComputedStyle(node)))
      .map((node) => ({node: node, depth: this._depth(node)}))
      .sort((left, right) => right.depth - left.depth).map((item) => item.node);
    this._layouts += 2;
    let fitted = 0;
    for (const card of cards) {
      if (fitted >= this.C.max_cards || !this._budgetLeft()) {
        this._partial = true;
        this._last.cards.push({qid: card.getAttribute('data-qid'), escape_px: null,
          grow_px: 0, factor: 1, outcome: 'skipped:budget', layouts: 0});
      } else if (this._fitCard(card, contentLine, qids)) fitted += 1;
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
      try { if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style); }
      catch (_) {}
    }
    this._snapshots = new Map(); this._applied = new Map(); this._layouts = 0; this._partial = false;
  },
  apply(pass) {
    this.reset();
    const requested = document.documentElement.classList.contains(this.C.fit_class);
    this._last = {version: this.C.version, requested: requested, fit_state: String(pass || 'provisional'),
      partial: false, error: null, cards: [], edits: []};
    if (!requested) return this._last;
    const timeout = Number(window.__codexFitTimeoutMs);
    this._deadline = Date.now() + this.C.time_budget_fraction *
      (Number.isFinite(timeout) && timeout > 0 ? timeout : this.C.default_timeout_ms);
    // A fitter defect must never cost the slide its metrics: missing metrics block publication.
    try {
      this._run();
      this._last.partial = this._partial; this._last.edits = this._editList();
    } catch (error) {
      this.reset();
      this._last.edits = []; this._last.partial = true;
      this._last.error = String((error && error.name) || 'Error') + ': ' +
        String((error && error.message) || error || '').slice(0, 200);
    }
    return this._last;
  },
  report() { return this._last; }
};
"""


def fit_script_fragment(width: int = 104) -> str:
    """The fragment the probe splices into its IIFE scope, with the constants interpolated
    as one object literal wrapped only between pairs, so every number stays greppable."""
    groups: list[str] = []
    for key, value in sorted(fit_constants().items()):
        pair = f"{json.dumps(key)}: {json.dumps(value)}"
        if groups and len(groups[-1]) + len(pair) + 2 <= width:
            groups[-1] += ", " + pair
        else:
            groups.append(pair)
    return _FRAGMENT.replace(_CONSTANTS_MARKER, "{" + ",\n    ".join(groups) + "}")


__all__ = ["DEFAULT_FIT_TIMEOUT_MS", "FIT_BISECTION_STEPS", "FIT_CLASS", "FIT_MARGIN_PX",
           "FIT_TIME_BUDGET_FRACTION", "FIT_VERSION", "MAX_FIT_CARDS", "MAX_FIT_LAYOUTS",
           "fit_constants", "fit_script_fragment"]
