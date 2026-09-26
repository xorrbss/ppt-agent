"""Browser local-fit script fragment, part A."""

FRAGMENT_A = r"""
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
"""
