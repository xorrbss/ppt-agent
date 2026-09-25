/** Read-only font observation before native wrappers or raster suppression. */
export interface LocalFitRunObservation {
  owner_qid: string;
  text_node_ordinal: number;
  start_offset: number;
  end_offset: number;
  content_digest: string;
  font_px: number;
  effective_text_scale: number | null;
  font_loaded: boolean;
  font_family: string;
  resolved_font_family?: string | null;
  font_resolution_source?: string;
}
export interface LocalFitObservation {
  runs: LocalFitRunObservation[];
  /** Transient source pixels; removed before durable evidence serialization. */
  local_image_sources?: { element_id: string; data_url: string;
    sourceBoundsPx: { x: number; y: number; width: number; height: number };
    run_indices: number[]; text_paint: "source" | "suppressed" }[];
  native_owners?: {
    element_id: string;
    owner_qid: string | null;
    run_indices: number[];
    owner_qids: string[];
    normalized_text_digest: string | null;
    emitted_runs: { content_digest: string; utf16_length: number;
      source_owner_qid: string; source_text_node_ordinal: number }[];
  }[];
  unowned_text_count: number;
  fonts_ready: boolean;
  viewport: { width: number; height: number; dpr: number };
}

export const LOCAL_FIT_BROWSER_SOURCE = String.raw`
var localFitOriginalTextNodes = new WeakMap();
var localFitExtractedRunSources = new WeakMap();
var localFitOriginalWhitespace = new WeakSet();
var localFitWhitespaceRuns = new WeakSet();
var localFitImageSources = new WeakMap();
function rememberLocalFitImage(element, bounds, payload, textPaint) {
  if (!payload) return;
  var indices = [], walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    var index = localFitOriginalTextNodes.get(walker.currentNode);
    if (index !== undefined) indices.push(index);
  }
  localFitImageSources.set(element, { data_url: payload.src, sourceBoundsPx: bounds,
    run_indices: indices, text_paint: textPaint });
}
function localFitTextDigest(text) {
  var value = 2166136261;
  for (var i = 0; i < text.length; i++) { value ^= text.charCodeAt(i); value = Math.imul(value, 16777619); }
  return 'fnv1a32:' + (value >>> 0).toString(16).padStart(8, '0');
}
function bindLocalFitElements(observation, records) {
  if (!observation) return;
  observation.local_image_sources = [];
  observation.native_owners = records.map(function (record) {
    var sourceImage = localFitImageSources.get(record.element);
    if (sourceImage) observation.local_image_sources.push(Object.assign({element_id: record.observation.id}, sourceImage));
    var owner = record.element.closest('[data-qid]'), indices = [];
    var walker = document.createTreeWalker(record.element, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      var index = localFitOriginalTextNodes.get(walker.currentNode);
      if (index !== undefined && indices.indexOf(index) < 0) indices.push(index);
    }
    var text = record.observation.text;
    var emitted = [];
    if (text) (text.runs || []).forEach(function (run, emittedIndex, emittedRuns) {
      var sourceIndex = localFitExtractedRunSources.get(run), source = observation.runs[sourceIndex];
      if (!source && localFitWhitespaceRuns.has(run) && /^[\t\f\v\r\n ]+$/.test(run.text)) {
        for (var distance = 1; distance < emittedRuns.length && !source; distance++) {
          var before = emittedRuns[emittedIndex - distance], after = emittedRuns[emittedIndex + distance];
          source = (before && observation.runs[localFitExtractedRunSources.get(before)]) ||
            (after && observation.runs[localFitExtractedRunSources.get(after)]);
        }
      }
      if (!source || !run.text) return;
      emitted.push({ content_digest: localFitTextDigest(run.text), utf16_length: run.text.length,
        source_owner_qid: source.owner_qid, source_text_node_ordinal: source.text_node_ordinal });
    });
    return { element_id: record.observation.id, owner_qid: owner ? owner.getAttribute('data-qid') : null,
      run_indices: indices, owner_qids: Array.from(new Set(indices.map(function (index) { return observation.runs[index].owner_qid; }))),
      normalized_text_digest: text ? localFitTextDigest(String(text.plainText || '').replace(/\s+/g, ' ').trim()) : null,
      emitted_runs: emitted };
  });
}
function observeLocalFitFonts() {
  var runs = [], ordinals = new Map(), unowned = 0, availableFamilies = new Map();
  var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  function fontFamilies(value) {
    var families = [], current = '', quote = null, escaped = false;
    for (var character of String(value || '')) {
      if (escaped) { current += character; escaped = false; continue; }
      if (character === '\\') { current += character; escaped = true; continue; }
      if (quote) {
        current += character;
        if (character === quote) quote = null;
        continue;
      }
      if (character === '"' || character === "'") { current += character; quote = character; continue; }
      if (character === ',') {
        if (current.trim()) families.push(current.trim().replace(/^["']|["']$/g, ''));
        current = ''; continue;
      }
      current += character;
    }
    if (current.trim()) families.push(current.trim().replace(/^["']|["']$/g, ''));
    return families;
  }
  function familyAvailable(family, raw, style) {
    var key = JSON.stringify([family.toLowerCase(), raw, style.fontStyle, style.fontWeight]);
    if (availableFamilies.has(key)) return availableFamilies.get(key);
    var context = document.createElement('canvas').getContext('2d'), available = false;
    if (context) {
      var sample = raw.trim(), descriptor = style.fontStyle + ' ' + style.fontWeight + ' 72px ';
      available = ['monospace', 'serif', 'sans-serif'].some(function (fallback) {
        context.font = descriptor + fallback; var base = context.measureText(sample).width;
        context.font = descriptor + '"__codex_missing_font__",' + fallback; var missing = context.measureText(sample).width;
        context.font = descriptor + JSON.stringify(family) + ',' + fallback; var actual = context.measureText(sample).width;
        return Math.abs(actual - base) > 0.01 && Math.abs(actual - missing) > 0.01;
      });
    }
    availableFamilies.set(key, available);
    return available;
  }
  function scaleFor(element) {
    if (element.closest('svg')) {
      if (typeof element.getScreenCTM !== 'function') return null;
      var svgMatrix = element.getScreenCTM();
      if (!svgMatrix || Math.abs(svgMatrix.b) > 1e-10 || Math.abs(svgMatrix.c) > 1e-10 ||
          svgMatrix.a <= 0 || Math.abs(svgMatrix.a - svgMatrix.d) > 1e-10) return null;
      return svgMatrix.a;
    }
    var scale = 1;
    for (var current = element; current; current = current.parentElement) {
      var style = getComputedStyle(current);
      if (style.transform && style.transform !== 'none') {
        var matrix = new DOMMatrixReadOnly(style.transform);
        if (!matrix.is2D || Math.abs(matrix.b) > 1e-10 || Math.abs(matrix.c) > 1e-10 ||
            matrix.a <= 0 || Math.abs(matrix.a - matrix.d) > 1e-10) return null;
        scale *= matrix.a;
      }
      var individual = style.scale;
      if (individual && individual !== 'none') {
        var parts = individual.trim().split(/\s+/).map(Number);
        if (!(parts[0] > 0) || (parts.length > 1 && parts[0] !== parts[1]) || parts.length > 2) return null;
        scale *= parts[0];
      }
      if (style.rotate && style.rotate !== 'none' && !/^0(?:deg|rad|turn)?$/.test(style.rotate)) return null;
      var zoom = parseFloat(style.zoom || '1');
      if (!(zoom > 0)) return null;
      scale *= zoom;
    }
    return scale;
  }
  while (walker.nextNode()) {
    var text = walker.currentNode, element = text.parentElement, raw = text.textContent || '';
    if (!element || element.closest('script,style,noscript,template')) continue;
    if (!raw.trim()) { localFitOriginalWhitespace.add(text); continue; }
    var hidden = false;
    for (var parent = element; parent; parent = parent.parentElement) {
      var parentStyle = getComputedStyle(parent);
      if (parentStyle.display === 'none' || parentStyle.visibility === 'hidden' || parentStyle.visibility === 'collapse') hidden = true;
    }
    if (hidden) continue;
    var owner = element.closest('[data-qid]');
    if (!owner) { unowned++; continue; }
    var qid = owner.getAttribute('data-qid'), ordinal = ordinals.get(qid) || 0;
    ordinals.set(qid, ordinal + 1);
    var style = getComputedStyle(element), fontPx = parseFloat(style.fontSize), fontLoaded = false;
    var resolvedFamily = null, resolutionSource = 'no-available-family';
    try {
      var stackReady = Boolean(document.fonts && document.fonts.status === 'loaded' &&
        document.fonts.check(style.fontStyle + ' ' + style.fontWeight + ' ' + style.fontSize + ' ' + style.fontFamily, raw));
      var generic = ['serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui'];
      if (!stackReady) resolutionSource = 'font-stack-not-ready';
      // FontFaceSet.check also succeeds for nonexistent local families. Require
      // an available member of the declared stack, in CSS fallback order.
      for (var family of stackReady ? fontFamilies(style.fontFamily) : []) {
        var source = null;
        if (generic.indexOf(family.toLowerCase()) >= 0) source = 'generic-fallback';
        else {
          var loadedFace = Array.from(document.fonts).some(function (face) {
            return String(face.family || '').replace(/^["']|["']$/g, '').toLowerCase() === family.toLowerCase() && face.status === 'loaded';
          });
          if (loadedFace && document.fonts.check(style.fontStyle + ' ' + style.fontWeight + ' ' + style.fontSize + ' ' + JSON.stringify(family), raw))
            source = 'loaded-font-face';
          else if (familyAvailable(family, raw, style)) source = 'installed-local-font';
        }
        if (source) { fontLoaded = true; resolvedFamily = family; resolutionSource = source; break; }
      }
    } catch (_) { fontLoaded = false; resolvedFamily = null; resolutionSource = 'font-probe-error'; }
    localFitOriginalTextNodes.set(text, runs.length);
    runs.push({ owner_qid: qid, text_node_ordinal: ordinal, start_offset: 0, end_offset: raw.length,
      content_digest: localFitTextDigest(raw), font_px: fontPx, effective_text_scale: scaleFor(element),
      font_loaded: fontLoaded, font_family: style.fontFamily,
      resolved_font_family: resolvedFamily, font_resolution_source: resolutionSource });
  }
  return { runs: runs, unowned_text_count: unowned,
    fonts_ready: Boolean(document.fonts && document.fonts.status === 'loaded'),
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio } };
}
`;
