"""Browser-side authored local fitter used by the Chromium DOM probe.

The fitter keeps HTML/CSS as the source of truth.  It evaluates the bounded
candidate set, appends only QID-addressable inline declarations, and reports the
exact appends for byte-preserving reconstruction by the API.
"""

from __future__ import annotations

import json

from services.authored_local_fit_policy import (
    FONT_FLOOR_PT,
    GEOMETRY_TOLERANCE_PX,
    MAX_SHRINK,
    POLICY_REVISION,
)
from utils.authored_dom_footer_zone import (
    NUMBERED_CONTENT_BOTTOM_PX,
    _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
    _PAGE_NUMBER_MAX_FONT_PX,
    _PAGE_NUMBER_MIN_TOP_FRACTION,
)
from utils.authored_dom_text_escape import (
    ESCAPE_TOLERANCE_PX,
    _PAINTED_TAGS,
    _TRANSPARENT,
)
from utils.slide_capture_fit_spacing import (
    COMBINED_CANDIDATES,
    FONT_ONLY_CANDIDATES,
    LIGHT_SPACING_CANDIDATES,
    SPACING_MEMBERS,
)
from utils.slide_capture_fit_observe import OBSERVE_MEMBERS
from utils.slide_capture_fit_compare import COMPARE_MEMBERS
from utils.slide_capture_fit_parent import PARENT_MEMBERS
from utils.slide_capture_fit_geometry import GEOMETRY_MEMBERS
from utils.slide_capture_fit_morph import MORPH_MEMBERS
from utils.slide_capture_fit_search import SEARCH_MEMBERS
from utils.slide_capture_fit_runner import RUNNER_MEMBERS

FIT_VERSION = POLICY_REVISION
MAX_FIT_REGIONS = 16
MAX_FIT_MEASUREMENTS = 160
DEFAULT_FIT_BUDGET_MS = 6000
MIN_SPACING_PX = 2.0
_CONSTANTS_MARKER = "__LOCAL_FIT_CONSTANTS__"
_OBSERVE_MARKER = "__LOCAL_OBSERVE_MEMBERS__"
_COMPARE_MARKER = "__LOCAL_COMPARE_MEMBERS__"
_SEARCH_MARKER = "__LOCAL_SEARCH_MEMBERS__"
_RUNNER_MARKER = "__LOCAL_RUNNER_MEMBERS__"
_SPACING_MARKER = "__LOCAL_SPACING_MEMBERS__"
_PARENT_MARKER = "__LOCAL_PARENT_MEMBERS__"
_GEOMETRY_MARKER = "__LOCAL_GEOMETRY_MEMBERS__"
_MORPH_MARKER = "__LOCAL_MORPH_MEMBERS__"


def fit_constants() -> dict[str, object]:
    return {
        "version": FIT_VERSION,
        "default_floor_pt": FONT_FLOOR_PT,
        "default_max_shrink": MAX_SHRINK,
        "default_preferred_max_shrink": MAX_SHRINK,
        "default_pt_per_css_px": 0.75,
        "default_budget_ms": DEFAULT_FIT_BUDGET_MS,
        "max_regions": MAX_FIT_REGIONS,
        "max_measurements": MAX_FIT_MEASUREMENTS,
        "geometry_tolerance_px": GEOMETRY_TOLERANCE_PX,
        "escape_tolerance_px": ESCAPE_TOLERANCE_PX,
        "min_spacing_px": MIN_SPACING_PX,
        "numbered_content_bottom_px": NUMBERED_CONTENT_BOTTOM_PX,
        "page_number_max_font_px": _PAGE_NUMBER_MAX_FONT_PX,
        "page_number_max_distance_px": _PAGE_NUMBER_MAX_DISTANCE_FROM_BOTTOM_PX,
        "page_number_min_top_fraction": _PAGE_NUMBER_MIN_TOP_FRACTION,
        "painted_tags": sorted(_PAINTED_TAGS),
        "transparent_backgrounds": sorted(_TRANSPARENT),
        "light_candidates": [list(value) for value in LIGHT_SPACING_CANDIDATES],
        "font_candidates": [list(value) for value in FONT_ONLY_CANDIDATES],
        "combined_candidates": [list(value) for value in COMBINED_CANDIDATES],
        "parent_reflow_factors": [1.0, 0.85, 0.70, 0.55, 0.50, 0.40],
    }


_FRAGMENT = r"""
const __codexFit = {
  C: __LOCAL_FIT_CONSTANTS__,
  P: (typeof __codexLocalFitPolicy === 'object' && __codexLocalFitPolicy) || {},
  _snapshots: new Map(), _structure: new Map(), _applied: new Map(),
  _baseline: null, _runs: [], _groups: [], _last: null, _lastFinalSignature: null,
  _measurements: 0, _deadline: 0, _candidateOrder: 0, _committedRuns: new Set(),
  _parentBaseApplied: new Map(),
  _number(value, fallback) {
    const result = Number(value); return Number.isFinite(result) ? result : fallback;
  },
  _px(value) { const result = parseFloat(value); return Number.isFinite(result) ? result : 0; },
  _round(value, digits) { const scale = Math.pow(10, digits); return Math.round(value * scale) / scale; },
  _ceil(value, digits) { const scale = Math.pow(10, digits); return Math.ceil(value * scale) / scale; },
  _cssPx(value) {
    const text = String(value === null || value === undefined ? '' : value).trim().toLowerCase();
    return text.endsWith('px') ? this._px(text.slice(0, -2)) : this._px(text);
  },
  _cssName(name) { return name.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase()); },
  _hash(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index); hash = Math.imul(hash, 16777619);
    }
    return 'fnv1a32:' + (hash >>> 0).toString(16).padStart(8, '0');
  },
  _budgetLeft() {
    const max = Math.min(this.C.max_measurements,
      Math.max(1, this._number(this.P.max_measurements, this.C.max_measurements)));
    return this._measurements < max && performance.now() < this._deadline;
  },
  _measure() { this._measurements += 1; return this._budgetLeft(); },
  _eligible(node) {
    if (!node || node === document.body || node === document.documentElement) return false;
    if (node.closest('svg') || node.closest('.ig')) return false;
    return !Array.from(node.classList || []).some((name) => name === 'ig' || name.indexOf('ig-') === 0);
  },
  _replayable(node) {
    const replayable = this.P._replayable_qids;
    return this._eligible(node) && Array.isArray(replayable) &&
      replayable.includes(String(node.getAttribute('data-qid') || ''));
  },
  _ownedTextNodes(node) {
    const result = [], walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!String(text.textContent || '').trim() || !text.parentElement) continue;
      if (text.parentElement.closest('[data-qid]') === node) result.push(text);
    }
    return result;
  },
  _snapshot(node) {
    if (!this._snapshots.has(node)) {
      const style = getComputedStyle(node);
      this._snapshots.set(node, {style: node.hasAttribute('style') ? node.getAttribute('style') : null,
        fontSize: this._px(style.fontSize), lineHeight: String(style.lineHeight || 'normal')});
    }
    return this._snapshots.get(node);
  },
  _explicitHeightPx(node) {
    const inline = node.style ? String(node.style.height || '').trim().toLowerCase() : '';
    if (inline) return inline.endsWith('px') ? this._cssPx(inline) : null;
    let declared = null;
    for (const sheet of Array.from(document.styleSheets || [])) {
      let rules = null; try { rules = sheet.cssRules; } catch (_) { continue; }
      for (const rule of Array.from(rules || [])) {
        const value = rule && rule.style ? String(rule.style.height || '').trim().toLowerCase() : '';
        let matched = false; try { matched = value ? node.matches(rule.selectorText) : false; } catch (_) { continue; }
        if (!matched) continue;
        if (!value.endsWith('px')) return null;
        declared = this._cssPx(value);
      }
    }
    return declared;
  },
  _struct(node) {
    if (this._structure.has(node)) return this._structure.get(node);
    const style = getComputedStyle(node); this._snapshot(node);
    const item = {display: String(style.display), flexDirection: String(style.flexDirection),
      position: String(style.position), transform: String(style.transform), scale: String(style.scale || 'none'),
      zoom: String(style.zoom || '1'),
      marginTop: this._px(style.marginTop), marginBottom: this._px(style.marginBottom),
      paddingTop: this._px(style.paddingTop), paddingBottom: this._px(style.paddingBottom),
      rowGap: this._px(style.rowGap), minHeight: this._px(style.minHeight),
      borderRight: this._px(style.borderRightWidth), borderBottom: this._px(style.borderBottomWidth),
      overflow: String(style.overflow), overflowX: String(style.overflowX), overflowY: String(style.overflowY),
      backgroundColor: String(style.backgroundColor), backgroundImage: String(style.backgroundImage),
      boxShadow: String(style.boxShadow), borderRadius: [style.borderTopLeftRadius, style.borderTopRightRadius,
        style.borderBottomRightRadius, style.borderBottomLeftRadius].some((value) => this._px(value) > 0),
      bordered: ['Top', 'Right', 'Bottom', 'Left'].every((side) => this._px(style['border' + side + 'Width']) > 0),
      explicitHeight: this._explicitHeightPx(node), ownText: this._ownedTextNodes(node).length > 0};
    this._structure.set(node, item); return item;
  },
  _isCardLike(node, item) {
    item = item || this._struct(node);
    const bg = item.backgroundColor.replace(/ /g, '').toLowerCase();
    return this.C.painted_tags.includes(node.tagName.toLowerCase()) ||
      Boolean(bg && !this.C.transparent_backgrounds.includes(bg)) || item.backgroundImage !== 'none' ||
      item.boxShadow !== 'none' || item.borderRadius || item.bordered ||
      [item.overflow, item.overflowX, item.overflowY].some((value) => value && value !== 'visible');
  },
  _entry(node) {
    if (!this._applied.has(node)) this._applied.set(node,
      {qid: node.getAttribute('data-qid'), text: null, spacing: null});
    return this._applied.get(node);
  },
  _declarations(entry) { return [entry && entry.text, entry && entry.spacing].filter(Boolean).join(';'); },
  _write(node) {
    const snap = this._snapshot(node), declarations = this._declarations(this._applied.get(node));
    if (declarations) node.setAttribute('style', (snap.style ? snap.style + ';' : '') + declarations);
    else {
      if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
      this._applied.delete(node);
    }
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
__LOCAL_OBSERVE_MEMBERS__
__LOCAL_COMPARE_MEMBERS__
__LOCAL_SPACING_MEMBERS__
__LOCAL_PARENT_MEMBERS__
__LOCAL_GEOMETRY_MEMBERS__
__LOCAL_MORPH_MEMBERS__
  _ink(node) {
    const rects = [], font = this._px(getComputedStyle(node).fontSize);
    for (const text of this._ownedTextNodes(node)) {
      const range = document.createRange(); range.selectNodeContents(text);
      for (const rect of range.getClientRects()) if (rect.width > 0 && rect.height > 0) rects.push(rect);
    }
    if (!rects.length) return null;
    return {left: Math.min(...rects.map((rect) => rect.left)), top: Math.min(...rects.map((rect) => rect.top)),
      right: Math.max(...rects.map((rect) => rect.right)),
      bottom: Math.max(...rects.map((rect) => rect.bottom - Math.max(0, (rect.height - font) / 2)))};
  },

  _boundaryTextInk(node) {
    const rects = [];
    for (const textNode of this._ownedTextNodes(node)) {
      if (!String(textNode.nodeValue || "").trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(textNode);
      for (const rect of Array.from(range.getClientRects())) {
        if (rect.width > 0 && rect.height > 0) rects.push(rect);
      }
    }
    if (!rects.length) return null;
    return {
      left: Math.min(...rects.map((rect) => rect.left)),
      top: Math.min(...rects.map((rect) => rect.top)),
      right: Math.max(...rects.map((rect) => rect.right)),
      bottom: Math.max(...rects.map((rect) => rect.bottom)),
    };
  },
  _escape(card, leaves, margin) {
    const style = getComputedStyle(card), rect = card.getBoundingClientRect();
    const right = rect.right - this._cssPx(style.borderRightWidth) - margin;
    const bottom = rect.bottom - this._cssPx(style.borderBottomWidth) - margin;
    let escape = 0;
    for (const ink of leaves.map((leaf) => this._ink(leaf)).filter(Boolean)) {
      // Match the whole-page quality gate by measuring trailing overflow only.
      escape = Math.max(escape, ink.right - right, ink.bottom - bottom);
    }
    return escape;
  },
  _flowBoxEscape(container) {
    const item = this._struct(container);
    if (item.explicitHeight === null ||
        !['block', 'flex', 'grid'].includes(item.display) ||
        !['static', 'relative'].includes(item.position)) return 0;
    const siblings = Array.from(container.parentElement ? container.parentElement.children : []);
    const ownIndex = siblings.indexOf(container);
    const following = siblings.slice(ownIndex + 1).filter((node) => {
      if (!node.hasAttribute('data-qid') || !this._eligible(node)) return false;
      const siblingItem = this._struct(node);
      return ['static', 'relative'].includes(siblingItem.position) && siblingItem.display !== 'none';
    });
    if (!following.length) return 0;
    const followingBoxes = following.map((node) => this._box(node));
    const box = this._box(container); let escape = 0;
    for (const child of Array.from(container.children)) {
      if (!child.hasAttribute('data-qid') || !this._eligible(child)) continue;
      const childItem = this._struct(child);
      if (!['static', 'relative'].includes(childItem.position) || childItem.display === 'none') continue;
      if (child.getAttribute('data-allow-overlap') === 'true' ||
          (!this._isCardLike(child, childItem) && !childItem.ownText)) continue;
      const childBox = this._box(child);
      if (!followingBoxes.some((siblingBox) => this._intersects(childBox, siblingBox))) continue;
      escape = Math.max(escape, box.left - childBox.left, box.top - childBox.top,
        childBox.right - box.right, childBox.bottom - box.bottom);
    }
    return escape;
  },
  _minimumPt(originalPt) {
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
    if (this._allowsExtendedShrink()) return floor;
    const shrink = this._number(this.P.max_shrink, this.C.default_max_shrink);
    return Math.max(floor, originalPt * (1 - shrink));
  },
  _slotFor(node) {
    const explicit = Array.isArray(this.P.fixed_slots) ? this.P.fixed_slots : [];
    for (const slot of explicit) {
      if (!slot) continue;
      const target = String(slot.qid || '');
      let owns = false;
      for (let current = node; current && current !== document.body; current = current.parentElement) {
        if (String(current.getAttribute && current.getAttribute('data-qid') || '') === target) {
          owns = true; break;
        }
      }
      if (!owns) continue;
      const raw = slot.bounds || slot.bbox || slot;
      const x = this._number(raw.x, this._number(raw.left, NaN));
      const y = this._number(raw.y, this._number(raw.top, NaN));
      const width = this._number(raw.width, NaN), height = this._number(raw.height, NaN);
      if ([x, y, width, height].every(Number.isFinite) && width > 0 && height > 0)
        return {kind: String(slot.kind || 'fixed_chrome'), x: x, y: y, width: width, height: height};
    }
    const contract = this.P.page_number_slot_contract;
    if (contract && contract.detector_revision === 'authored-footer-number/v1') {
      for (let owner = node; owner && owner !== document.body; owner = owner.parentElement) {
        if (!owner.hasAttribute('data-qid')) continue;
        const rect = owner.getBoundingClientRect(), font = this._px(getComputedStyle(node).fontSize);
        const text = String(owner.innerText || owner.textContent || '').trim();
        if (rect.left <= 2 && rect.right >= innerWidth - 4 &&
            innerHeight - rect.bottom >= 0 && innerHeight - rect.bottom <= this.C.page_number_max_distance_px &&
            rect.top >= innerHeight * this.C.page_number_min_top_fraction && font > 0 &&
            font <= this.C.page_number_max_font_px && /^\d{1,3}$/.test(text) &&
            Number(text) === contract.expected_number) {
          return {kind:'page_number', x:0, y:this.C.numbered_content_bottom_px,
            width:innerWidth, height:innerHeight - this.C.numbered_content_bottom_px,
            proof:{detector_revision:contract.detector_revision, owner_qid:owner.getAttribute('data-qid'),
              expected_number:contract.expected_number}};
        }
      }
    }
    return null;
  },
  _inside(box, slot) {
    const tolerance = this.C.geometry_tolerance_px;
    return box.left >= slot.x - tolerance && box.top >= slot.y - tolerance &&
      box.right <= slot.x + slot.width + tolerance && box.bottom <= slot.y + slot.height + tolerance;
  },
  _normalizationPlan() {
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const plan = [], failures = [], seen = new Set();
    for (const run of this._runs) {
      const minimum = this._minimumPt(run.original_pt), current = run.current_pt;
      if (current !== null && current >= minimum) continue;
      const node = run.node, qid = run.owner_qid;
      if (seen.has(qid)) continue; seen.add(qid);
      if (!run.editable || this._runs.some((item) => item.node === node && !item.editable)) {
        failures.push('unsupported_geometry'); continue;
      }
      if (!this._replayable(node)) { failures.push('reconstruction_mismatch'); continue; }
      const position = this._struct(node).position, slot = this._slotFor(node), card = this._nearestCard(node);
      if (['absolute', 'fixed'].includes(position) && !slot) {
        failures.push('unsupported_geometry'); continue;
      }
      if (!['absolute', 'fixed'].includes(position) && !card) {
        failures.push('font_floor_unfit'); continue;
      }
      const targetPt = Math.max(floor, ...this._runs.filter((item) => item.node === node)
        .map((item) => this._minimumPt(item.original_pt)));
      const scale = this._effectiveScale(node);
      if (!(scale > 0)) { failures.push('font_unverifiable'); continue; }
      const targetPx = this._ceil(targetPt / (ptPerPx * scale), 4), snap = this._snapshot(node);
      const parts = ['font-size:' + targetPx + 'px'];
      const lineHeight = String(snap.lineHeight || '').trim().toLowerCase();
      if (lineHeight.endsWith('px') && this._cssPx(lineHeight) < targetPx)
        parts.push('line-height:' + this._ceil(targetPx, 4) + 'px');
      plan.push({node: node, kind: 'text', declarations: parts.join(';'), slot: slot,
        operation: {kind: current < floor ? 'floor_normalized' : 'baseline_restored', qid: qid,
          fixed_slot: slot,
          from_pt: this._round(current || 0, 4), to_pt: this._round(targetPt, 4)}});
    }
    return {plan: plan, failures: failures};
  },
  _applyPlan(plan) {
    if (plan.some((item) => !this._replayable(item.node)))
      throw new Error('source_qid_unavailable');
    const changed = new Set();
    for (const item of plan) {
      const entry = this._entry(item.node); entry[item.kind] = item.declarations;
      this._write(item.node); changed.add(item.node);
    }
    return Array.from(changed);
  },
  _box(node) {
    const rect = node.getBoundingClientRect();
    return {left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom,
      width: rect.width, height: rect.height};
  },
  _sameBox(first, second) {
    return ['left', 'top', 'width', 'height'].every((key) =>
      Math.abs(first[key] - second[key]) <= this.C.geometry_tolerance_px);
  },
  _intersects(first, second) {
    return Math.min(first.right, second.right) > Math.max(first.left, second.left) &&
      Math.min(first.bottom, second.bottom) > Math.max(first.top, second.top);
  },
  _validateFixed(plan, before) {
    for (const item of plan.filter((value) => value.slot)) {
      const box = this._box(item.node); if (!this._inside(box, item.slot)) return false;
      for (const [other, oldBox] of before) {
        if (other === item.node || item.node.contains(other) || other.contains(item.node)) continue;
        const now = this._box(other);
        if (!this._intersects(oldBox, before.get(item.node)) && this._intersects(now, box)) return false;
      }
    }
    return true;
  },
  _globalGeometryHard(before, qids) {
    const affected = new Set();
    for (const card of this._last.cards) {
      const node = qids.find((item) => item.getAttribute('data-qid') === card.qid);
      if (node) affected.add(node);
    }
    for (const operation of this._last.operations.filter((item) =>
      item.kind === 'floor_normalized' || item.kind === 'baseline_restored')) {
      const node = qids.find((item) => item.getAttribute('data-qid') === operation.qid);
      const card = node && this._nearestCard(node); if (card) affected.add(card);
    }
    for (const node of qids) {
      const slot = this._slotFor(node);
      if (slot) { if (!this._inside(this._box(node), slot)) return false; else continue; }
      const owner = Array.from(affected).find((card) => card === node || card.contains(node));
      if (owner && owner !== node) continue;
      const initial = before.get(node);
      if (initial && !this._sameBox(initial, this._box(node)) && !this._committedRootGrowthAllowed(node, initial)) return false;
    }
    return true;
  },
  _regionLeaves(card) {
    return Array.from(card.querySelectorAll('[data-qid]')).filter((node) => {
      if (!this._eligible(node) || this._ownedTextNodes(node).length === 0) return false;
      for (let current = node; current && current !== card; current = current.parentElement) {
        if (['absolute', 'fixed'].includes(this._struct(current).position)) return false;
      }
      return true;
    });
  },
  _regions(qids) {
    const found = [];
    for (const card of qids.filter((node) => {
      const item = this._struct(node);
      const fixedFlowContainer = item.explicitHeight !== null &&
        ['block', 'flex', 'grid'].includes(item.display) &&
        ['static', 'relative'].includes(item.position);
      return this._isCardLike(node, item) || fixedFlowContainer;
    })) {
      const leaves = this._regionLeaves(card); if (!leaves.length) continue;
      const conflicts = this._flowHeightConflicts(card, leaves), escape = this._escape(card, leaves, 0);
      const flowBoxEscape = this._flowBoxEscape(card);
      if (escape > this.C.escape_tolerance_px || flowBoxEscape > this.C.escape_tolerance_px || conflicts.length)
        found.push({card: card, leaves: leaves, conflicts: conflicts,
          escape_px: Math.max(escape, flowBoxEscape), flow_box_escape_px: flowBoxEscape});
    }
    const set = new Set(found.map((item) => item.card));
    return found.filter((item) => !Array.from(set).some((child) => child !== item.card && item.card.contains(child)))
      .sort((left, right) => qids.indexOf(left.card) - qids.indexOf(right.card));
  },
__LOCAL_SEARCH_MEMBERS__
  _finalRuns() {
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    return this._runs.map((run) => {
      const scale = this._effectiveScale(run.measure_node), finalPt = scale === null ? null :
        this._px(getComputedStyle(run.measure_node).fontSize) * scale * ptPerPx;
      const fontEvidence = this._fontEvidence(run.measure_node,
        String(run.text_node && run.text_node.textContent || ''));
      return {run_id: run.run_id, owner_qid: run.owner_qid, content_digest: run.content_digest,
        text_node_ordinal: run.text_node_ordinal, start_offset: run.start_offset, end_offset: run.end_offset,
        ancestor_qids: run.ancestor_qids,
        group_id: run.group_id, role: run.role, original_pt: run.original_pt || 0,
        final_pt: finalPt,
        minimum_allowed_pt: this._minimumPt(run.original_pt),
        preferred_minimum_pt: this._preferredMinimumPt(run.original_pt), exceeds_preferred_shrink:
          finalPt !== null && finalPt < this._preferredMinimumPt(run.original_pt) - 1e-10,
        shrink_ratio: finalPt === null ? null : this._round(Math.max(0, 1 - finalPt / run.original_pt), 6),
        font_loaded: fontEvidence.loaded, resolved_font_family: fontEvidence.family,
        font_resolution_source: fontEvidence.source};
    });
  },
  _editList() {
    const qids = Array.from(document.querySelectorAll('[data-qid]'));
    return Array.from(this._applied.entries()).map(([node, entry]) => ({node: node,
      qid: entry.qid || node.getAttribute('data-qid'), declarations: this._declarations(entry)}))
      .filter((item) => item.declarations).sort((left, right) => qids.indexOf(left.node) - qids.indexOf(right.node))
      .map((item) => ({qid: item.qid, declarations: item.declarations}));
  },
  _status() {
    if (this._last.unresolved_reasons.length || this._last.partial || this._last.error) return 'unresolved';
    const kinds = new Set(this._last.operations.map((item) => item.kind));
    const floor = kinds.has('floor_normalized') || kinds.has('baseline_restored');
    const spacing = kinds.has('spacing_adjusted') || kinds.has('parent_reflow');
    const text = kinds.has('text_scaled'), combined = kinds.has('combined');
    if (combined || (spacing && text)) return 'combined';
    if (text) return 'text_scaled'; if (spacing) return 'spacing_adjusted'; if (floor) return 'floor_normalized';
    return 'unchanged';
  },
  _restoreAll() {
    for (const [node, snap] of this._snapshots) {
      if (snap.style === null) node.removeAttribute('style'); else node.setAttribute('style', snap.style);
    }
    this._applied = new Map(); this._committedRuns = new Set();
  },
__LOCAL_RUNNER_MEMBERS__
};
"""
def fit_script_fragment() -> str:
    constants = json.dumps(fit_constants(), sort_keys=True, separators=(",", ":"))
    return (
        _FRAGMENT.replace(_CONSTANTS_MARKER, constants)
        .replace(_OBSERVE_MARKER, OBSERVE_MEMBERS)
        .replace(_COMPARE_MARKER, COMPARE_MEMBERS)
        .replace(_SEARCH_MARKER, SEARCH_MEMBERS)
        .replace(_SPACING_MARKER, SPACING_MEMBERS)
        .replace(_PARENT_MARKER, PARENT_MEMBERS)
        .replace(_GEOMETRY_MARKER, GEOMETRY_MEMBERS)
        .replace(_MORPH_MARKER, MORPH_MEMBERS)
        .replace(_RUNNER_MARKER, RUNNER_MEMBERS)
)
__all__ = [
    "DEFAULT_FIT_BUDGET_MS",
    "FIT_VERSION",
    "GEOMETRY_TOLERANCE_PX",
    "MAX_FIT_MEASUREMENTS",
    "MAX_FIT_REGIONS",
    "MIN_SPACING_PX",
    "fit_constants",
    "fit_script_fragment",
]
