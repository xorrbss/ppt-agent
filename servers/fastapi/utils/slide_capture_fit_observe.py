"""Browser fragment for authored text-run and font evidence collection."""

OBSERVE_MEMBERS = r"""
  _baselineGroupIdentity(run) {
    const supplied = this.P.baseline || this._baseline;
    const group = supplied && Array.isArray(supplied.groups) ?
      supplied.groups.find(item => item.group_id === run.group_id) : null;
    // CSS-only morphology can remove a painted-card boundary. The original
    // group identity remains authoritative after run/content identity matching.
    return group || {card_qid: run.nearest_card_qid, role: run.role};
  },
  _effectiveScale(node) {
    if (node && node.closest && node.closest('svg')) {
      if (typeof node.getScreenCTM !== 'function') return null;
      const matrix = node.getScreenCTM();
      if (!matrix || Math.abs(matrix.b) > 1e-10 || Math.abs(matrix.c) > 1e-10 ||
          !(matrix.a > 0) || Math.abs(matrix.a - matrix.d) > 1e-10) return null;
      return matrix.a;
    }
    let effective = 1;
    for (let current = node; current; current = current.parentElement) {
      const style = getComputedStyle(current), transform = String(style.transform || 'none');
      if (transform !== 'none') {
        let matrix = null; try { matrix = new DOMMatrixReadOnly(transform); } catch (_) { return null; }
        if (!matrix.is2D || Math.abs(matrix.b) > 1e-10 || Math.abs(matrix.c) > 1e-10 ||
            !(matrix.a > 0) || Math.abs(matrix.a - matrix.d) > 1e-10) return null;
        effective *= matrix.a;
      }
      const scaleText = String(style.scale || 'none').trim();
      if (scaleText && scaleText !== 'none') {
        const values = scaleText.split(/\s+/).map(Number);
        if (!values.length || values.some((value) => !Number.isFinite(value) || value <= 0)) return null;
        if (values.length > 1 && Math.abs(values[0] - values[1]) > 1e-7) return null;
        effective *= values[0];
      }
      if (style.rotate && style.rotate !== 'none' && !/^0(?:deg|rad|turn)?$/.test(style.rotate)) return null;
      const zoom = this._number(style.zoom, 1); if (!(zoom > 0)) return null;
      effective *= zoom;
    }
    return effective;
  },
  _fontFamilies(value) {
    const families = []; let current = '', quote = null, escaped = false;
    for (const character of String(value || '')) {
      if (escaped) { current += character; escaped = false; continue; }
      if (character === '\\') { current += character; escaped = true; continue; }
      if (quote) {
        current += character;
        if (character === quote) quote = null;
        continue;
      }
      if (character === '"' || character === "'") { current += character; quote = character; continue; }
      if (character === ',') {
        if (current.trim()) families.push(current.trim().replace(/^['"]|['"]$/g, ''));
        current = ''; continue;
      }
      current += character;
    }
    if (current.trim()) families.push(current.trim().replace(/^['"]|['"]$/g, ''));
    return families;
  },
  _familyAvailable(family, text) {
    const canvas = document.createElement('canvas'), context = canvas.getContext('2d');
    if (!context) return false;
    const sample = String(text || '').trim() || 'AaMmWw09\uD55C\uAE00\u6F22\u5B57';
    return ['monospace', 'serif', 'sans-serif'].some((fallback) => {
      context.font = '72px ' + fallback; const base = context.measureText(sample).width;
      context.font = '72px "__codex_missing_font__",' + fallback;
      const missing = context.measureText(sample).width;
      context.font = '72px ' + JSON.stringify(family) + ',' + fallback;
      const actual = context.measureText(sample).width;
      return Math.abs(actual - base) > 0.01 && Math.abs(actual - missing) > 0.01;
    });
  },
  _fontEvidence(node, text) {
    if (!document.fonts) return {loaded: true, family: null, source: 'font-api-unavailable'};
    const style = getComputedStyle(node);
    try {
      if (document.fonts.status !== 'loaded') return {loaded: false, family: null, source: 'fonts-pending'};
      const generic = new Set(['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui']);
      for (const family of this._fontFamilies(style.fontFamily)) {
        const normalized = family.toLowerCase();
        if (generic.has(normalized)) return {loaded: true, family: family, source: 'generic-fallback'};
        const loadedFace = Array.from(document.fonts).some((font) =>
          String(font.family || '').replace(/^['"]|['"]$/g, '').toLowerCase() === normalized &&
          font.status === 'loaded');
        if (loadedFace) {
          const descriptor = [style.fontStyle, style.fontWeight, style.fontSize,
            JSON.stringify(family)].join(' ');
          if (document.fonts.check(descriptor, String(text || '').trim() || 'M'))
            return {loaded: true, family: family, source: 'loaded-font-face'};
        }
        if (this._familyAvailable(family, text))
          return {loaded: true, family: family, source: 'installed-local-font'};
      }
      return {loaded: false, family: null, source: 'no-available-family'};
    } catch (_) { return {loaded: false, family: null, source: 'font-probe-error'}; }
  },
  _fontLoaded(node, text) {
    return this._fontEvidence(node, text).loaded;
  },
  _role(node) {
    const explicit = String(node.getAttribute('data-semantic-role') || node.getAttribute('data-role') ||
      node.getAttribute('role') || '').toLowerCase();
    if (['title', 'heading', 'body', 'label', 'supporting', 'caption', 'note'].includes(explicit)) return explicit;
    if (/^h[1-6]$/i.test(node.tagName)) return 'title';
    return 'unknown';
  },
  _nearestCard(node) {
    for (let current = node; current && current !== document.body; current = current.parentElement) {
      if (current.hasAttribute('data-qid') && this._isCardLike(current)) return current;
    }
    return null;
  },
  _baselineRunMap() {
    const source = this.P.baseline || (this._baseline && this._baseline.complete ? this._baseline : null);
    const supplied = source && source.runs;
    const values = Array.isArray(supplied) ? supplied :
      supplied && typeof supplied === 'object' ? Object.values(supplied) : [];
    return new Map(values.filter((item) => item && item.run_id).map((item) => [String(item.run_id), item]));
  },
  _collectRuns() {
    const ptPerPx = this._number(this.P.pt_per_css_px,
      this._number(this.P.export_scale_manifest && this.P.export_scale_manifest.pt_per_css_px,
        this.C.default_pt_per_css_px));
    const authorRevision = String(this.P.author_revision || ''), baselineMap = this._baselineRunMap();
    const supplied = Boolean(this.P.baseline || (this._baseline && this._baseline.complete));
    let complete = Boolean(authorRevision && this.P.original_html_digest && ptPerPx > 0), unowned = 0;
    const entries = [], ordinals = new Map(), walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      const measureNode = text.parentElement, raw = String(text.textContent || '');
      if (!measureNode || !raw.trim() || measureNode.closest('script,style,noscript,template')) continue;
      let hidden = false;
      for (let current = measureNode; current; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
          hidden = true; break;
        }
      }
      if (hidden) continue;
      const node = measureNode.closest('[data-qid]');
      if (!node) { unowned += 1; continue; }
      const qid = String(node.getAttribute('data-qid') || ''), ordinal = ordinals.get(qid) || 0;
      ordinals.set(qid, ordinal + 1); entries.push({node: node, measureNode: measureNode,
        text: text, raw: raw, qid: qid, ordinal: ordinal});
    }
    let generated = 0;
    for (const element of Array.from(document.body.querySelectorAll('*'))) {
      let hidden = false;
      for (let current = element; current; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
          hidden = true; break;
        }
      }
      if (hidden) continue;
      for (const pseudo of ['::before', '::after']) {
        const content = String(getComputedStyle(element, pseudo).content || '').trim();
        if (content && !['none', 'normal', '""', "''"].includes(content)) generated += 1;
      }
    }
    if (unowned || generated) complete = false;
    const runs = [];
    for (const entry of entries) {
        const node = entry.node, measureNode = entry.measureNode, text = entry.text;
        const raw = entry.raw, qid = entry.qid, ordinal = entry.ordinal, style = getComputedStyle(measureNode);
        const scale = this._effectiveScale(measureNode), fontPx = this._px(style.fontSize);
        const runId = [authorRevision, qid, ordinal, 0, raw.length].join(':');
        const prior = baselineMap.get(runId), contentDigest = this._hash(raw);
        const currentPt = scale && fontPx > 0 && ptPerPx > 0 ? fontPx * scale * ptPerPx : null;
        const fontEvidence = this._fontEvidence(measureNode, raw), fontLoaded = fontEvidence.loaded;
        const originalPt = prior ? this._number(prior.original_pt, NaN) : currentPt;
        if (!(originalPt > 0) || !(currentPt > 0) || !fontLoaded || scale === null) complete = false;
        if (supplied && (!prior || String(prior.content_digest || '') !== contentDigest)) complete = false;
        const editable = measureNode === node && this._eligible(node);
        const card = editable ? this._nearestCard(node) : null;
        const role = prior && prior.role ? String(prior.role) : this._role(node), ancestorQids = [];
        for (let current = measureNode; current; current = current.parentElement) {
          const ancestorQid = current.getAttribute && current.getAttribute('data-qid');
          if (ancestorQid && !ancestorQids.includes(String(ancestorQid))) ancestorQids.push(String(ancestorQid));
        }
        const fingerprint = this._hash([style.fontFamily, style.fontWeight, style.fontStyle,
          String(originalPt || 0), style.lineHeight, style.letterSpacing, style.writingMode].join('|'));
        const groupId = prior && prior.group_id ? String(prior.group_id) :
          [card ? card.getAttribute('data-qid') : qid, role, fingerprint].join('|');
        runs.push({node: node, measure_node: measureNode, text_node: text, editable: editable,
          run_id: runId, owner_qid: qid, text_node_ordinal: ordinal, ancestor_qids: ancestorQids,
          start_offset: 0, end_offset: raw.length, content_digest: contentDigest, group_id: groupId,
          role: role, original_pt: originalPt, current_pt: currentPt, font_px: fontPx,
          effective_text_scale: scale, font_loaded: fontLoaded,
          resolved_font_family: fontEvidence.family, font_resolution_source: fontEvidence.source,
          nearest_card_qid: card ? card.getAttribute('data-qid') : null});
    }
    if (supplied && baselineMap.size !== runs.length) complete = false;
    const groupMap = new Map();
    for (const run of runs) {
      if (!groupMap.has(run.group_id)) groupMap.set(run.group_id,
        {group_id: run.group_id, role: this._baselineGroupIdentity(run).role, card_qid: this._baselineGroupIdentity(run).card_qid,
          run_ids: [], original_pt: run.original_pt});
      const group = groupMap.get(run.group_id); group.run_ids.push(run.run_id);
      group.original_pt = Math.min(group.original_pt, run.original_pt);
    }
    const fontIdentity = Array.from(document.fonts || []).map((font) =>
      [font.family, font.style, font.weight, font.status].join(':')).sort().join('|');
    const measurementIdentity = this._hash([authorRevision, this.P.original_html_digest,
      innerWidth, innerHeight, devicePixelRatio, ptPerPx, fontIdentity,
      this.P.policy_revision || this.C.version].join('|'));
    this._runs = runs; this._groups = Array.from(groupMap.values());
    this._baseline = {author_revision: authorRevision,
      original_html_digest: String(this.P.original_html_digest || ''),
      runs: runs.map((run) => ({run_id: run.run_id, owner_qid: run.owner_qid,
        text_node_ordinal: run.text_node_ordinal, start_offset: run.start_offset, end_offset: run.end_offset,
        ancestor_qids: run.ancestor_qids, content_digest: run.content_digest, group_id: run.group_id,
        role: run.role, original_pt: run.original_pt || 0, font_px: run.font_px,
        effective_text_scale: run.effective_text_scale, font_loaded: run.font_loaded,
        resolved_font_family: run.resolved_font_family,
        font_resolution_source: run.font_resolution_source})),
      groups: this._groups, unowned_text_count: unowned, generated_text_count: generated,
      measurement_identity: measurementIdentity, complete: complete};
    return complete;
  },
"""

__all__ = ["OBSERVE_MEMBERS"]
