"""Flow archetypes: process, timeline, roadmap, swimlane, branch.

The two orbit archetypes (cycle, hub_spoke) carry enough geometry of their own
to live in ``css_orbit``; what stays here lays out along a line. The timeline
root is ``.ig-milestones``, not ``.ig-timeline``: the hybrid exporter reads a
whole-word ``timeline`` class token on a wrapper as a raster island (see
docs/authored-native-figure-contract-20260903.md).
"""

from __future__ import annotations

from utils.authored_infographic.css_orbit import ORBIT_CSS

PROCESS_CSS = """\
.ig-process{display:flex;align-items:stretch;justify-content:center;gap:0;padding:8px 0}
.ig-process .ig-node{flex:1 1 0;max-width:280px;justify-content:center}
.ig-process .ig-no{width:28px;height:28px;border-radius:14px;background:$primary_tint;color:$primary_deep;
  line-height:28px;font-size:15px}
.ig-process .ig-node.is-key .ig-no{background:$white;color:$primary_deep}
"""

MILESTONES_CSS = """\
.ig-milestones .ig-rail{left:4%;right:4%;top:62px;width:92%}
.ig-milestones .ig-track{position:absolute;left:0;right:0;top:0;bottom:0;display:flex;
  justify-content:space-between;align-items:stretch;gap:16px}
.ig-milestones .ig-node{flex:1 1 0;padding:53px 14px 20px;gap:10px;justify-content:flex-start}
.ig-milestones .ig-node-bg{border-radius:14px;box-shadow:none;background:$surface}
.ig-milestones .ig-when{position:relative;margin-top:auto;font-size:15px;font-weight:800;color:$primary;
  line-height:20px}
.ig-milestones .ig-node > :last-child{margin-bottom:auto}
.ig-milestones .ig-node.is-key .ig-node-bg{background:$primary;border:1px solid $primary}
.ig-milestones .ig-node.is-key .ig-when{color:$on_primary_muted}
"""

ROADMAP_CSS = """\
.ig-roadmap{display:flex;flex-direction:column;justify-content:stretch;gap:14px;padding:8px 0}
.ig-roadmap .ig-node{flex:1 1 0;flex-direction:row;align-items:center;text-align:left;gap:20px;
  padding:12px 18px}
.ig-roadmap .ig-node-bg{border-radius:12px;box-shadow:none;background:$surface}
.ig-roadmap .ig-node.is-key .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft;box-shadow:none}
.ig-roadmap .ig-node.is-key .ig-label,.ig-roadmap .ig-node.is-key .ig-num{color:$primary_deep}
.ig-roadmap .ig-node.is-key .ig-desc,.ig-roadmap .ig-node.is-key .ig-no,.ig-roadmap .ig-node.is-key .ig-unit{
  color:$muted}
.ig-roadmap .ig-node.is-key .ig-ic{background:$primary;color:$on_primary}
.ig-roadmap .ig-head{position:relative;width:260px;flex:none;display:flex;flex-direction:column;gap:4px}
.ig-roadmap .ig-track{position:relative;flex:1 1 0;min-width:0;height:34px;border-radius:8px;background:$white;
  border:1px solid $line;overflow:hidden}
.ig-roadmap .ig-fill{position:absolute;top:5px;height:22px;border-radius:6px;background:$primary_soft}
.ig-roadmap .ig-node.is-key .ig-fill{background:$primary}
.ig-fill.o0{left:1%}
.ig-fill.o20{left:20%}
.ig-fill.o40{left:40%}
.ig-fill.o60{left:60%}
.ig-fill.o80{left:80%}
.ig-fill.w20{width:19%}
.ig-fill.w40{width:39%}
.ig-fill.w60{width:59%}
.ig-fill.w80{width:79%}
.ig-fill.w100{width:98%}
.ig-fill.o20.w100{width:79%}
.ig-fill.o40.w80,.ig-fill.o40.w100{width:59%}
.ig-fill.o60.w60,.ig-fill.o60.w80,.ig-fill.o60.w100{width:39%}
.ig-fill.o80.w40,.ig-fill.o80.w60,.ig-fill.o80.w80,.ig-fill.o80.w100{width:19%}
.ig-roadmap .ig-tail{position:relative;width:120px;flex:none;display:flex;justify-content:flex-end}
"""

SWIMLANE_CSS = """\
.ig-swimlane{display:flex;flex-direction:column;gap:10px;justify-content:stretch}
.ig-swimlane .ig-node{flex:1 1 0;flex-direction:row;align-items:center;text-align:left;gap:18px;
  padding:12px 20px}
.ig-swimlane .ig-node-bg{border-radius:12px;box-shadow:none;background:$surface}
.ig-swimlane .ig-node.is-key .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft;box-shadow:none}
.ig-swimlane .ig-node.is-key .ig-label{color:$primary_deep}
.ig-swimlane .ig-node.is-key .ig-desc,.ig-swimlane .ig-node.is-key .ig-no{color:$muted}
.ig-swimlane .ig-node.is-key .ig-ic{background:$primary;color:$on_primary}
.ig-swimlane .ig-head{position:relative;width:220px;flex:none;display:flex;flex-direction:column;gap:2px}
.ig-swimlane .ig-steps{position:relative;flex:1 1 0;display:flex;align-items:center;gap:8px}
.ig-swimlane .ig-step{position:relative;flex:1 1 0;height:40px;border-radius:8px;background:$white;
  border:1px solid $line;font-size:15px;font-weight:600;color:$ink;line-height:38px;text-align:center;padding:0 8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ig-swimlane .ig-node.is-key .ig-step{border:1px solid $primary_soft;color:$primary_deep}
.ig-swimlane .ig-step-arrow{flex:none;width:10px;height:10px;border-top:2px solid $primary_soft;
  border-right:2px solid $primary_soft;transform:rotate(45deg)}
"""

BRANCH_CSS = """\
.ig-branch{display:grid;grid-template-columns:1fr 80px 1fr;grid-template-rows:minmax(0,1fr);
  align-items:center;column-gap:0}
.ig-branch .ig-root{align-self:center;padding:26px 22px}
.ig-branch .ig-root .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft;box-shadow:none}
.ig-branch .ig-root .ig-label{color:$primary_deep;font-size:26px}
.ig-branch .ig-root.is-key .ig-node-bg{background:$primary;border:1px solid $primary}
.ig-branch .ig-root.is-key .ig-label{color:$on_primary}
.ig-branch .ig-fork{position:relative;height:100%;min-height:200px}
.ig-branch .ig-fork-v{position:absolute;left:39px;top:12%;height:76%;width:2px;background:$primary_soft}
.ig-branch .ig-fork-in{position:absolute;left:0;top:50%;width:40px;height:2px;background:$primary_soft}
.ig-branch .ig-outcomes{display:flex;flex-direction:column;justify-content:space-between;gap:16px;height:100%}
.ig-branch .ig-outcomes .ig-node{flex-direction:row;text-align:left;align-items:center;gap:16px;padding:14px 18px}
.ig-branch .ig-outcomes .ig-node-bg{border-radius:12px}
"""

FLOW_CSS = (
    PROCESS_CSS + ORBIT_CSS + MILESTONES_CSS + ROADMAP_CSS + SWIMLANE_CSS + BRANCH_CSS
)

__all__ = ["FLOW_CSS"]
