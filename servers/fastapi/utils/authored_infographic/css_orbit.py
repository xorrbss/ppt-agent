"""Orbit archetypes: cycle and hub_spoke.

Both arrange fixed 200px station plates around a centred hub, but their
connectors differ, so their station positions are stated separately rather
than shared: the cycle's plates must leave the loop's arrowheads visible,
while the hub_spoke's must line up with the radial spokes.

The geometry is stated for the canvas the contract asks for (at least
900x420px, a hero page being about 1150x560px) and every position is an
explicit ``left``/``top`` offset from the centre, so an arrowhead is placed
by coordinates and rotated in place — a pure rotation the exporter keeps
native, never a translate/rotate chain.

Three and four stations sit on a circle of radius 170px. Five and six do not
fit on a circle in a 560px-tall canvas — three stacked plate heights do not
exist — so their plates form two rows and the loop becomes a 520x340 ellipse
whose arrowheads land in the corridors between the rows.
"""

from __future__ import annotations

ORBIT_BASE_CSS = """\
.ig-cycle,.ig-hub-spoke{min-height:420px}
.ig-cycle .ig-node,.ig-hub-spoke .ig-node{position:absolute;width:200px;margin-left:-100px;padding:14px 12px;gap:5px}
"""

CYCLE_CSS = """\
.ig-cycle .ig-node.is-key{width:220px;margin-left:-110px}
.ig-cycle .ig-node.is-key .ig-node-bg{border-radius:20px}
.ig-ring{position:absolute;left:50%;top:50%;width:340px;height:340px;margin-left:-170px;margin-top:-170px;
  border-radius:50%;border:4px solid $primary_soft}
.ig-ring-head{position:absolute;margin-left:-11px;margin-top:-11px;width:22px;height:22px;border-radius:3px;
  border-top:5px solid $primary;border-right:5px solid $primary}
.ig-cycle.n3 .ig-node.p1{left:50%;top:0}
.ig-cycle.n3 .ig-node.p2{left:calc(50% + 260px);top:calc(50% + 50px)}
.ig-cycle.n3 .ig-node.p3{left:calc(50% - 260px);top:calc(50% + 50px)}
.ig-cycle.n3 .ig-ring-head.h1{left:calc(50% + 147px);top:calc(50% - 85px);transform:rotate(105deg)}
.ig-cycle.n3 .ig-ring-head.h2{left:50%;top:calc(50% + 170px);transform:rotate(225deg)}
.ig-cycle.n3 .ig-ring-head.h3{left:calc(50% - 147px);top:calc(50% - 85px);transform:rotate(345deg)}
.ig-cycle.n4 .ig-node.p1{left:50%;top:0}
.ig-cycle.n4 .ig-node.p2{left:calc(50% + 260px);top:calc(50% - 60px)}
.ig-cycle.n4 .ig-node.p3{left:50%;bottom:0}
.ig-cycle.n4 .ig-node.p4{left:calc(50% - 260px);top:calc(50% - 60px)}
.ig-cycle.n4 .ig-ring-head.h1{left:calc(50% + 120px);top:calc(50% - 120px);transform:rotate(90deg)}
.ig-cycle.n4 .ig-ring-head.h2{left:calc(50% + 120px);top:calc(50% + 120px);transform:rotate(180deg)}
.ig-cycle.n4 .ig-ring-head.h3{left:calc(50% - 120px);top:calc(50% + 120px);transform:rotate(270deg)}
.ig-cycle.n4 .ig-ring-head.h4{left:calc(50% - 120px);top:calc(50% - 120px);transform:rotate(0deg)}
.ig-cycle.n5 .ig-ring,.ig-cycle.n6 .ig-ring{width:520px;margin-left:-260px}
.ig-cycle.n5 .ig-node.p1{left:50%;top:0}
.ig-cycle.n5 .ig-node.p2{left:calc(50% + 250px);top:0}
.ig-cycle.n5 .ig-node.p3{left:calc(50% + 160px);bottom:0}
.ig-cycle.n5 .ig-node.p4{left:calc(50% - 160px);bottom:0}
.ig-cycle.n5 .ig-node.p5{left:calc(50% - 250px);top:0}
.ig-cycle.n5 .ig-ring-head.h1{left:calc(50% + 124px);top:calc(50% - 149px);transform:rotate(65deg)}
.ig-cycle.n5 .ig-ring-head.h2{left:calc(50% + 260px);top:50%;transform:rotate(135deg)}
.ig-cycle.n5 .ig-ring-head.h3{left:50%;top:calc(50% + 170px);transform:rotate(225deg)}
.ig-cycle.n5 .ig-ring-head.h4{left:calc(50% - 260px);top:50%;transform:rotate(315deg)}
.ig-cycle.n5 .ig-ring-head.h5{left:calc(50% - 124px);top:calc(50% - 149px);transform:rotate(25deg)}
.ig-cycle.n6 .ig-node.p1{left:50%;top:0}
.ig-cycle.n6 .ig-node.p2{left:calc(50% + 250px);top:0}
.ig-cycle.n6 .ig-node.p3{left:calc(50% + 250px);bottom:0}
.ig-cycle.n6 .ig-node.p4{left:50%;bottom:0}
.ig-cycle.n6 .ig-node.p5{left:calc(50% - 250px);bottom:0}
.ig-cycle.n6 .ig-node.p6{left:calc(50% - 250px);top:0}
.ig-cycle.n6 .ig-ring-head.h1{left:calc(50% + 124px);top:calc(50% - 149px);transform:rotate(65deg)}
.ig-cycle.n6 .ig-ring-head.h2{left:calc(50% + 260px);top:50%;transform:rotate(135deg)}
.ig-cycle.n6 .ig-ring-head.h3{left:calc(50% + 124px);top:calc(50% + 149px);transform:rotate(205deg)}
.ig-cycle.n6 .ig-ring-head.h4{left:calc(50% - 124px);top:calc(50% + 149px);transform:rotate(245deg)}
.ig-cycle.n6 .ig-ring-head.h5{left:calc(50% - 260px);top:50%;transform:rotate(315deg)}
.ig-cycle.n6 .ig-ring-head.h6{left:calc(50% - 124px);top:calc(50% - 149px);transform:rotate(25deg)}
"""

HUB_SPOKE_CSS = """\
.ig-hub-spoke .ig-hub{width:260px;margin-left:-130px;height:130px;margin-top:-65px;z-index:1}
.ig-hub-spoke .ig-hub-bg{background:$primary;border:1px solid $primary;box-shadow:0 18px 36px $primary_glow}
.ig-hub-spoke .ig-hub-title{color:$on_primary}
.ig-hub-spoke .ig-hub-sub{color:$on_primary_muted}
.ig-hub-spoke .ig-node.is-key .ig-node-bg{background:$primary_deep;border:1px solid $primary_deep}
.ig-spoke{position:absolute;left:50%;top:50%;width:2px;height:170px;margin-left:-1px;margin-top:-170px;
  background:$primary_soft;transform-origin:50% 100%}
.ig-hub-spoke.n3 .ig-node.p1{left:50%;top:0}
.ig-hub-spoke.n3 .ig-node.p2{left:calc(50% + 260px);top:calc(50% + 40px)}
.ig-hub-spoke.n3 .ig-node.p3{left:calc(50% - 260px);top:calc(50% + 40px)}
.ig-hub-spoke.n3 .ig-spoke.k1{transform:rotate(0deg)}
.ig-hub-spoke.n3 .ig-spoke.k2{transform:rotate(118deg)}
.ig-hub-spoke.n3 .ig-spoke.k3{transform:rotate(242deg)}
.ig-hub-spoke.n4 .ig-node.p1{left:50%;top:0}
.ig-hub-spoke.n4 .ig-node.p2{left:calc(50% + 260px);top:calc(50% - 60px)}
.ig-hub-spoke.n4 .ig-node.p3{left:50%;bottom:0}
.ig-hub-spoke.n4 .ig-node.p4{left:calc(50% - 260px);top:calc(50% - 60px)}
.ig-hub-spoke.n4 .ig-spoke.k1{transform:rotate(0deg)}
.ig-hub-spoke.n4 .ig-spoke.k2{transform:rotate(99deg)}
.ig-hub-spoke.n4 .ig-spoke.k3{transform:rotate(180deg)}
.ig-hub-spoke.n4 .ig-spoke.k4{transform:rotate(261deg)}
.ig-hub-spoke.n5 .ig-node.p1{left:50%;top:0}
.ig-hub-spoke.n5 .ig-node.p2{left:calc(50% + 250px);top:calc(50% - 130px)}
.ig-hub-spoke.n5 .ig-node.p3{left:calc(50% + 160px);bottom:0}
.ig-hub-spoke.n5 .ig-node.p4{left:calc(50% - 160px);bottom:0}
.ig-hub-spoke.n5 .ig-node.p5{left:calc(50% - 250px);top:calc(50% - 130px)}
.ig-hub-spoke.n5 .ig-spoke.k1{transform:rotate(0deg)}
.ig-hub-spoke.n5 .ig-spoke.k2{transform:rotate(81deg)}
.ig-hub-spoke.n5 .ig-spoke.k3{transform:rotate(140deg)}
.ig-hub-spoke.n5 .ig-spoke.k4{transform:rotate(220deg)}
.ig-hub-spoke.n5 .ig-spoke.k5{transform:rotate(279deg)}
"""

#: Three stations leave the least room between the top plate and the plates
#: below it, so the hub gives that band back rather than crossing a plate.
ORBIT_HUB_CSS = """\
.ig.n3 .ig-hub{height:100px;margin-top:-50px}
.ig.n5 .ig-hub,.ig.n6 .ig-hub{width:260px;margin-left:-130px}
"""

ORBIT_CSS = ORBIT_BASE_CSS + CYCLE_CSS + HUB_SPOKE_CSS + ORBIT_HUB_CSS

__all__ = ["ORBIT_CSS"]
