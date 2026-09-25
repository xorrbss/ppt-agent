"""Policy-aware comparison helpers for browser local-fit candidates."""

COMPARE_MEMBERS = r"""
  _policyRevision() { return String(this.P.policy_revision || this.C.version); },
  _allowsExtendedShrink() {
    return this.P.allow_extended_shrink === true && this._policyRevision() === 'authored-local-fit/v1.5';
  },
  _preferredMaxShrink() {
    return Math.max(0, this._number(this.P.preferred_max_shrink,
      this._number(this.P.max_shrink, this.C.default_preferred_max_shrink)));
  },
  _preferredMinimumPt(originalPt) {
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
    return Math.max(floor, originalPt * (1 - this._preferredMaxShrink()));
  },
  _candidateRejection(reason, stage, details) {
    const names = {font_unverifiable:'reject.font_unverifiable', unverifiable_font_size:'reject.font_unverifiable',
      relative_shrink_limit:'reject.relative_shrink_limit', parent_space_unavailable:'reject.parent_space_unfit',
      parent_growth_cap:'reject.parent_space_unfit', protected_boundary:'reject.scope_exceeded',
      parent_preservation:'reject.scope_exceeded', outside_geometry:'reject.scope_exceeded',
      duplicate_declarations:'reject.duplicate_declarations', budget_exhausted:'reject.budget_exhausted'};
    return Object.assign({code: names[reason] || 'reject.' + reason, stage: stage}, details || {});
  },
  _extendedFontFactors(region) {
    const floor = this._number(this.P.floor_pt, this.C.default_floor_pt);
    const preferred = 1 - this._preferredMaxShrink(), defectSet = new Set(region.defectLeaves || []);
    const groups = new Set(this._runs.filter(run => defectSet.has(run.node)).map(run => run.group_id));
    const floorFactors = this._runs.filter(run => groups.has(run.group_id) && run.original_pt > floor)
      .map(run => floor / run.original_pt);
    const ladder = [0.875, 0.85, 0.825, 0.80, 0.775, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50];
    return Array.from(new Set(ladder.concat(floorFactors).filter(value => value > 0 && value < preferred - 1e-10)
      .map(value => this._round(value, 8)))).sort((a, b) => b - a);
  },
  _extendedComparison(rootQid) {
    const records = this._last.candidates.filter(item => item.root_qid === rootQid && item.extended_shrink !== true);
    const compared = [], rejected = [], uncertain = [];
    for (const record of records) {
      const outcome = String(record.outcome || ''), rejection = record.rejection || {};
      if (['passed', 'committed'].includes(outcome) && record.cost && Number.isFinite(Number(record.cost.total))) {
        compared.push(record.candidate_id); continue;
      }
      const knownRejection = outcome.startsWith('rejected:') || outcome.startsWith('removed:') || outcome.startsWith('pruned:');
      const uncertainReason = outcome.startsWith('unmeasured:') || rejection.code === 'reject.font_unverifiable' ||
        rejection.code === 'reject.budget_exhausted';
      if (knownRejection && !uncertainReason && String(rejection.code || '').startsWith('reject.'))
        rejected.push(record.candidate_id);
      else uncertain.push(record.candidate_id);
    }
    return {comparison_complete: records.length > 0 && compared.length > 0 && uncertain.length === 0,
      comparison_uncertain: uncertain.length > 0, compared_candidate_ids: compared,
      rejected_candidate_ids: rejected, uncertain_candidate_ids: uncertain};
  },
"""

__all__ = ["COMPARE_MEMBERS"]
