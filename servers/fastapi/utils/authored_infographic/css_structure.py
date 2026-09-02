"""Structure archetypes: hierarchy, layers, funnel, pyramid, venn."""

from __future__ import annotations

HIERARCHY_CSS = """\
.ig-hierarchy{display:flex;flex-direction:column;align-items:center;justify-content:center}
.ig-hierarchy .ig-root{width:320px;padding:16px 20px}
.ig-hierarchy .ig-root .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft;box-shadow:none}
.ig-hierarchy .ig-root .ig-label{color:$primary_deep}
.ig-hierarchy .ig-root.is-key .ig-node-bg{background:$primary}
.ig-hierarchy .ig-root.is-key .ig-label{color:$on_primary}
.ig-hierarchy .ig-bar{width:70%}
.ig-hierarchy .ig-children{display:flex;justify-content:center;align-items:stretch;gap:20px;width:100%;padding-top:0}
.ig-hierarchy .ig-children .ig-node{flex:1 1 0;max-width:260px;padding:16px 14px}
.ig-hierarchy .ig-children .ig-stem{align-self:center}
.ig-hierarchy .ig-child{display:flex;flex-direction:column;align-items:center;flex:1 1 0;max-width:260px}
.ig-hierarchy .ig-child .ig-node{width:100%}
"""

LAYERS_CSS = """\
.ig-layers{display:flex;flex-direction:column;justify-content:center;gap:10px;padding:0 6%}
.ig-layers .ig-node{flex-direction:row;align-items:center;text-align:left;gap:20px;padding:14px 26px;flex:1 1 0;
  min-height:64px}
.ig-layers .ig-node-bg{border-radius:12px;box-shadow:none;background:$surface}
.ig-layers .ig-node.l1 .ig-node-bg{background:$surface}
.ig-layers .ig-node.l2 .ig-node-bg{background:$primary_tint}
.ig-layers .ig-node.l3 .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft}
.ig-layers .ig-node.l4 .ig-node-bg{background:$primary_soft;border:1px solid $primary_soft}
.ig-layers .ig-node.l5 .ig-node-bg,.ig-layers .ig-node.l6 .ig-node-bg{background:$primary_soft;
  border:1px solid $primary}
.ig-layers .ig-node.is-key .ig-node-bg{background:$primary;border:1px solid $primary}
.ig-layers .ig-node.l4 .ig-desc,.ig-layers .ig-node.l5 .ig-desc,.ig-layers .ig-node.l6 .ig-desc,
  .ig-layers .ig-node.l4 .ig-unit,.ig-layers .ig-node.l5 .ig-unit,.ig-layers .ig-node.l6 .ig-unit{color:$ink}
.ig-layers .ig-node.is-key .ig-desc,.ig-layers .ig-node.is-key .ig-unit{color:$on_primary_muted}
.ig-layers .ig-text{position:relative;display:flex;flex-direction:column;gap:3px;flex:1 1 0}
.ig-layers .ig-tail{position:relative;flex:none;text-align:right}
.ig-layers .ig-tail .ig-num{font-size:30px}
"""

FUNNEL_CSS = """\
.ig-funnel,.ig-pyramid{display:flex;flex-direction:column;align-items:center;justify-content:stretch;gap:8px}
.ig-funnel .ig-node,.ig-pyramid .ig-node{flex:1 1 0;flex-direction:row;align-items:center;
  justify-content:space-between;text-align:left;gap:20px;padding:12px 28px;min-height:60px}
.ig-funnel .ig-node-bg,.ig-pyramid .ig-node-bg{border-radius:10px;box-shadow:none;background:$primary_tint;
  border:1px solid $primary_soft}
.ig-funnel .ig-node.is-key .ig-node-bg,.ig-pyramid .ig-node.is-key .ig-node-bg{background:$primary;
  border:1px solid $primary}
.ig-funnel .ig-text,.ig-pyramid .ig-text{position:relative;display:flex;align-items:baseline;gap:12px;min-width:0}
.ig-funnel .ig-tail,.ig-pyramid .ig-tail{position:relative;flex:none}
.ig-funnel .ig-node.f1{width:100%}
.ig-funnel .ig-node.f2{width:86%}
.ig-funnel .ig-node.f3{width:72%}
.ig-funnel .ig-node.f4{width:58%}
.ig-funnel .ig-node.f5{width:46%}
.ig-funnel .ig-node.f6{width:36%}
.ig-pyramid .ig-node.f1{width:36%}
.ig-pyramid .ig-node.f2{width:48%}
.ig-pyramid .ig-node.f3{width:60%}
.ig-pyramid .ig-node.f4{width:72%}
.ig-pyramid .ig-node.f5{width:84%}
.ig-pyramid .ig-node.f6{width:96%}
.ig-funnel .ig-num,.ig-pyramid .ig-num{font-size:30px}
.ig.n3 .ig-funnel-gap,.ig-funnel.n3 .ig-node,.ig-pyramid.n3 .ig-node{min-height:88px}
"""

VENN_CSS = """\
.ig-venn{min-height:400px}
.ig-set{position:absolute;width:300px;height:300px;border-radius:150px;background:$primary_tint;
  border:2px solid $primary_soft;opacity:0.85}
.ig-set.s2{background:$primary_soft}
.ig-set.s3{background:$surface;border:2px solid $primary}
.ig.n3 .ig-set.s1{left:calc(50% - 250px);top:20px}
.ig.n3 .ig-set.s2{left:calc(50% - 50px);top:20px}
.ig.n3 .ig-set.s3{left:calc(50% - 150px);top:150px}
.ig.n4 .ig-set.s1{left:calc(50% - 250px);top:20px}
.ig.n4 .ig-set.s2{left:calc(50% - 50px);top:20px}
.ig.n4 .ig-set.s3{left:calc(50% - 150px);top:150px}
.ig-venn .ig-node{position:absolute;width:210px;margin-left:-105px;padding:10px;gap:4px}
.ig-venn .ig-node-bg{display:none}
.ig-venn .ig-node.v1{left:calc(50% - 170px);top:40px}
.ig-venn .ig-node.v2{left:calc(50% + 170px);top:40px}
.ig-venn .ig-node.v3{left:50%;top:340px}
.ig-venn .ig-node.v4{left:50%;top:150px;width:200px;margin-left:-100px}
.ig-venn .ig-node.v4 .ig-node-bg{display:block;background:$primary;border:1px solid $primary;border-radius:16px}
.ig-venn .ig-node.v4 .ig-label{color:$on_primary}
.ig-venn .ig-node.v4 .ig-desc{color:$on_primary_muted}
.ig-venn .ig-node.is-key .ig-node-bg{display:block;background:$primary;border:1px solid $primary;
  border-radius:16px}
.ig-venn .ig-core{position:absolute;left:50%;top:150px;width:200px;margin-left:-100px;text-align:center;
  font-size:20px;font-weight:800;color:$primary_deep;line-height:1.3;word-break:keep-all}
"""

STRUCTURE_CSS = HIERARCHY_CSS + LAYERS_CSS + FUNNEL_CSS + VENN_CSS

__all__ = ["STRUCTURE_CSS"]
