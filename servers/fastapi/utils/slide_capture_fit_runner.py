"""Browser fragment that runs fit and emits the acceptance report."""

RUNNER_MEMBERS = r"""
  _imageTextCheck() {
    const evidenceSource = this.P.image_text_evidence || {};
    const values = Array.isArray(evidenceSource) ? evidenceSource : Object.values(evidenceSource);
    const evidence = values.filter((item) => item && typeof item === 'object');
    const targets = Array.from(document.querySelectorAll('img,object,embed,canvas,svg image')).concat(
      Array.from(document.querySelectorAll('[data-qid]')).filter((node) =>
        /url\(/i.test(String(getComputedStyle(node).backgroundImage || '')))
    );
    const records = [], failures = [];
    for (const node of Array.from(new Set(targets))) {
      let visible = true;
      for (let current = node; current; current = current.parentElement) {
        const currentStyle = getComputedStyle(current);
        if (currentStyle.display === 'none' || ['hidden', 'collapse'].includes(currentStyle.visibility) ||
            Number(currentStyle.opacity) === 0) { visible = false; break; }
      }
      const bounds = node.getBoundingClientRect();
      if (!visible || bounds.width <= 0 || bounds.height <= 0 ||
          bounds.right <= 0 || bounds.bottom <= 0 || bounds.left >= innerWidth || bounds.top >= innerHeight) continue;
      const owner = node.closest && node.closest('[data-qid]');
      const qid = String(owner && owner.getAttribute('data-qid') || '');
      const style = getComputedStyle(node);
      const identity = this._hash([node.tagName, node.currentSrc || node.getAttribute('src') ||
        node.getAttribute('href') || node.getAttribute('data') || '', style.backgroundImage].join('|'));
      const proof = evidence.find((item) => String(item.qid || '') === qid ||
        String(item.asset_digest || item.identity || '') === identity);
      const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
      const noText = Boolean(proof && proof.text_free === true && proof.review_digest);
      const minimumPt = proof ? this._number(proof.minimum_text_pt, NaN) : NaN;
      const sizedText = Boolean(proof && proof.measurement_identity && minimumPt >= floor);
      const verified = noText || sizedText;
      records.push({qid: qid, asset_identity: identity, verified: verified,
        scope: noText ? 'verified_no_text' : sizedText ? 'verified_text_size' : 'unverifiable'});
      if (!verified) failures.push('font_unverifiable');
    }
    return {records: records, failures: failures};
  },
  _emptyReport(pass, requested) {
    return {version: this.C.version, policy_id: String(this.P.policy_id || ''),
      policy_revision: String(this.P.policy_revision || this.C.version), requested: requested,
      fit_state: String(pass || 'provisional'), status: 'unchanged', changed: false, partial: false,
      error: null, unresolved_reasons: [], operations: [], edits: [], baseline: null,
      runs: [], groups: [], image_text: [], cards: [], candidates: [], scopes: [], search_termination: [],
      geometry_seeds: [], parent_exclusions: [], telemetry: {candidate_total: 0,
        evaluated: 0, policy_removed: 0, duplicates: 0, pruned: 0, measurements: 0, fit_search_ms: 0}};
  },
  apply(pass) {
    const requested = Boolean(this.P.fit), state = String(pass || 'provisional');
    if (state === 'final') {
      this._measurements = 0; this._candidateOrder = 0;
      if (this._last && this._last.fit_state === 'final') {
        const stableBaseline = this._baseline && this._baseline.complete ? this._baseline : null;
        this._restoreAll(); this._structure = new Map(); this._runs = []; this._groups = [];
        this._baseline = stableBaseline;
        if (!stableBaseline) this._snapshots = new Map();
      } else if (this._last) {
        this._snapshots = new Map(); this._structure = new Map(); this._applied = new Map();
        this._committedRuns = new Set(); this._runs = []; this._groups = []; this._baseline = null;
      }
    }
    this._last = this._emptyReport(state, requested); const started = performance.now();
    const fontsReady = !document.fonts || document.fonts.status === 'loaded';
    const baselineComplete = this._collectRuns(); this._last.baseline = this._baseline;
    if (state !== 'final' || !fontsReady) {
      this._last.baseline = Object.assign({}, this._baseline, {complete: false});
      this._last.runs = this._finalRuns(); return this._last;
    }
    if (!baselineComplete) {
      const measurementFailed = this._runs.some(run => !(run.original_pt > 0) || !(run.current_pt > 0) ||
        run.effective_text_scale === null || !run.font_loaded);
      this._last.unresolved_reasons.push(measurementFailed ? 'font_unverifiable' : 'baseline_unavailable');
    }
    const imageText = this._imageTextCheck(); this._last.image_text = imageText.records;
    this._last.unresolved_reasons.push(...imageText.failures);
    if (!requested) {
      this._last.status = this._last.unresolved_reasons.length ? 'unresolved' : 'unchanged';
      this._last.runs = this._finalRuns(); this._last.groups = this._groups; return this._last;
    }
    const budget = Math.min(this.C.default_budget_ms,
      Math.max(1, this._number(this.P.fit_budget_ms, this.C.default_budget_ms)));
    this._deadline = performance.now() + budget;
    try {
      if (!baselineComplete) throw new Error(this._last.unresolved_reasons[0]);
      const qids = Array.from(document.querySelectorAll('[data-qid]')).filter((node) => this._eligible(node));
      for (const node of qids) this._struct(node);
      const before = new Map(qids.map((node) => [node, this._box(node)]));
      const normalized = this._normalizationPlan();
      const normalizedOcclusion = new Map(normalized.plan.map((item) =>
        [item.node, this._textOccluded(item.node)]));
      this._last.unresolved_reasons.push(...normalized.failures);
      if (!normalized.failures.length) {
        this._applyPlan(normalized.plan);
        if (!this._validateFixed(normalized.plan, before)) this._last.unresolved_reasons.push('fixed_chrome_unfit');
        else this._last.operations.push(...normalized.plan.map((item) => item.operation).filter(Boolean));
      }
      if (!this._last.unresolved_reasons.length) {
        const detected = this._regions(qids), regions = this._fitScopes(this._boundaryRegions(qids, detected), qids);
        const limit = Math.min(this.C.max_regions, Math.max(1, this._number(this.P.max_regions, this.C.max_regions)));
        if (detected.length + this._last.geometry_seeds.length > limit) this._last.unresolved_reasons.push('budget_exhausted');
        else for (const region of regions) if (!this._fitRegion(region, qids)) break;
      }
      if (!this._last.unresolved_reasons.length &&
          normalized.plan.some((item) => !normalizedOcclusion.get(item.node) && this._textOccluded(item.node)))
        this._last.unresolved_reasons.push('fixed_chrome_unfit');
      if (!this._last.unresolved_reasons.length && !this._globalGeometryHard(before, qids))
        this._last.unresolved_reasons.push('reconstruction_mismatch');
      if (!this._last.unresolved_reasons.length &&
          !this._fontHard(Array.from(new Set(this._runs.map((run) => run.node)))))
        this._last.unresolved_reasons.push('hierarchy_unfit');
      const finalRuns = this._finalRuns();
      if (finalRuns.some((run) => !(run.final_pt > 0)))
        this._last.unresolved_reasons.push('font_unverifiable');
      if (finalRuns.some((run) => run.final_pt > 0 &&
          run.final_pt < this._number(this.P.floor_pt, this.C.default_floor_pt)))
        this._last.unresolved_reasons.push('font_floor_unfit');
      if (!this._allowsExtendedShrink() && finalRuns.some((run) => run.final_pt > 0 &&
          run.final_pt >= this._number(this.P.floor_pt, this.C.default_floor_pt) &&
          run.final_pt < run.minimum_allowed_pt))
        this._last.unresolved_reasons.push('relative_shrink_limit');
      this._last.unresolved_reasons = Array.from(new Set(this._last.unresolved_reasons));
      if (this._last.unresolved_reasons.length) {
        this._restoreAll(); this._last.operations = []; this._last.edits = []; this._last.cards = [];
        this._last.runs = this._finalRuns();
      } else {
        this._last.edits = this._editList(); this._last.runs = finalRuns;
      }
    } catch (error) {
      this._restoreAll(); this._last.partial = true; this._last.operations = []; this._last.edits = []; this._last.cards = [];
      this._last.error = String(error && (error.message || error) || 'fit failed').slice(0, 240);
      if (this._last.error === 'unverifiable_font_size') this._last.unresolved_reasons.push('font_unverifiable');
      if (this._last.error === 'source_qid_unavailable') this._last.unresolved_reasons.push('reconstruction_mismatch');
      this._last.runs = this._finalRuns();
    }
    this._last.groups = this._groups; this._last.changed = this._last.edits.length > 0;
    this._last.status = this._status(); this._last.telemetry.candidate_total = this._last.candidates.length;
    this._last.telemetry.measurements = this._measurements;
    this._last.telemetry.fit_search_ms = this._round(performance.now() - started, 3);
    return this._last;
  },
  report() { return this._last; }
"""

__all__ = ["RUNNER_MEMBERS"]
