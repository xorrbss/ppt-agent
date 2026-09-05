"""Shared primitives of the archetype library: root, node, connectors, density.

Rules every rule set in this package follows, because the renderer's CSS
policy and the hybrid exporter's text measurer both depend on them:

* no CSS custom properties and no ``content`` — tokens are ``$literals``;
* no ``::before``/``::after`` at all — every mark is a real, empty element;
* an element that contains text carries no background paint, gradient,
  shadow, rotation or radius of its own — the ``*-bg`` sibling behind it does;
* border-radius is always one uniform pixel value;
* no declared ``font-size`` is below 15px. The generation pipeline clamps the
  whole document — this stylesheet included — to the frozen slide capacity's
  ``min_font_px`` (15) before the first Chromium render, so a smaller value
  here would be a size no browser ever applies, and the fit it implies would
  be measured against a stylesheet that does not exist.

Fitting is the other invariant this file carries. Every ``.ig-node`` is a flex
item, and a flex item's default ``min-height:auto`` refuses to shrink below
its content: six stacked rows of intrinsic height simply grew past the box the
authoring model gave the figure and ran off the canvas. ``min-height:0`` here,
and the ``n5``/``n6`` compaction in ``css_compact``, are what make the rows
share the box instead.
"""

from __future__ import annotations

BASE_CSS = """\
.ig-lib-v1{display:none}
.ig{position:relative;width:100%;height:100%;margin:0;font-family:$font;color:$ink;line-height:1.35;box-sizing:border-box}
.ig *{box-sizing:border-box;margin:0;padding:0}
.ig-node{position:relative;display:flex;flex-direction:column;align-items:center;text-align:center;padding:18px 16px;
  gap:6px;min-width:0;min-height:0}
.ig-node-bg{position:absolute;left:0;top:0;width:100%;height:100%;border-radius:16px;background:$white;
  border:1px solid $line;box-shadow:0 10px 24px rgba(15,23,42,0.08),0 2px 5px rgba(15,23,42,0.05)}
.ig-ic,.ig-no,.ig-label,.ig-desc,.ig-figure,.ig-chips{position:relative}
.ig-label,.ig-desc,.ig-figure,.ig-text{min-width:0}
.ig-ic{width:46px;height:46px;border-radius:12px;background:$primary_tint;color:$primary;display:flex;
  align-items:center;justify-content:center;flex:none}
.ig-ic svg{width:24px;height:24px;display:block;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;
  stroke-linejoin:round}
.ig-ic img{width:24px;height:24px;display:block;object-fit:contain}
.ig-no{font-size:15px;font-weight:800;color:$primary;line-height:18px}
.ig-label{font-size:22px;font-weight:700;line-height:1.25;color:$ink;word-break:keep-all;overflow-wrap:anywhere}
.ig-desc{font-size:15px;font-weight:500;color:$muted;line-height:1.35;word-break:keep-all;overflow-wrap:anywhere}
.ig-figure{display:flex;align-items:baseline;justify-content:center;gap:5px}
.ig-num{font-size:44px;font-weight:800;line-height:1;color:$primary_deep}
.ig-unit{font-size:15px;font-weight:600;color:$muted}
.ig-chips{display:flex;flex-wrap:wrap;gap:6px;justify-content:center}
.ig-chip{position:relative;height:24px;padding:0 10px;border-radius:7px;background:$primary_tint;color:$primary_deep;
  font-size:15px;font-weight:700;line-height:24px;white-space:nowrap}
.ig-node.is-key .ig-node-bg{background:$primary;border:1px solid $primary;
  box-shadow:0 18px 36px $primary_glow,0 4px 10px rgba(15,23,42,0.10)}
.ig-node.is-key .ig-ic{background:rgba(255,255,255,0.22);color:$on_primary}
.ig-node.is-key .ig-no,.ig-node.is-key .ig-desc,.ig-node.is-key .ig-unit{color:$on_primary_muted}
.ig-node.is-key .ig-label,.ig-node.is-key .ig-num{color:$on_primary}
.ig-node.is-key .ig-chip{background:$white;color:$primary_deep}
.ig-caption{position:absolute;left:0;bottom:0;font-size:15px;color:$muted;padding-left:12px;
  border-left:3px solid $primary;line-height:20px}
.ig-hub{position:absolute;left:50%;top:50%;width:300px;margin-left:-150px;height:120px;margin-top:-60px;display:flex;
  flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:6px;padding:0 16px}
.ig-hub-bg{position:absolute;left:0;top:0;width:100%;height:100%;border-radius:24px;background:$surface;
  border:1px solid $line}
.ig-hub-title{position:relative;font-size:22px;font-weight:800;color:$ink;line-height:1.25;word-break:keep-all;
  overflow-wrap:anywhere}
.ig-hub-sub{position:relative;font-size:15px;font-weight:500;color:$muted;line-height:1.3;word-break:keep-all;
  overflow-wrap:anywhere}
.ig-arrow{position:relative;flex:none;width:36px;align-self:center;height:36px}
.ig-arrow-head{position:absolute;left:8px;top:11px;width:14px;height:14px;border-top:3px solid $primary_soft;
  border-right:3px solid $primary_soft;transform:rotate(45deg)}
.ig-arrow-line{position:absolute;left:0;top:17px;width:26px;height:2px;background:$primary_soft}
.ig-rail{position:absolute;height:3px;background:$primary_soft;border-radius:2px}
.ig-stem{width:2px;height:30px;background:$primary_soft;flex:none}
.ig-bar{height:2px;background:$primary_soft;flex:none}
.ig-dot{position:relative;width:18px;height:18px;border-radius:9px;background:$primary;border:3px solid $white;
  box-shadow:0 0 0 2px $primary_soft;flex:none}
.ig-node.is-key .ig-dot{background:$primary_deep;box-shadow:0 0 0 4px $primary_soft}
.ig.n3 .ig-label{font-size:26px}
.ig.n3 .ig-desc{font-size:17px}
.ig.n3 .ig-num{font-size:52px}
.ig.n5 .ig-label{font-size:20px;line-height:1.2}
.ig.n5 .ig-desc{font-size:15px;line-height:1.25}
.ig.n5 .ig-num{font-size:38px}
.ig.n5 .ig-node{padding:14px 14px;gap:5px}
.ig.n5 .ig-ic{width:40px;height:40px}
.ig.n5 .ig-ic svg,.ig.n5 .ig-ic img{width:22px;height:22px}
.ig.n6 .ig-label{font-size:18px;line-height:1.15}
.ig.n6 .ig-desc{font-size:15px;line-height:1.2}
.ig.n6 .ig-num{font-size:32px}
.ig.n6 .ig-node{padding:10px 12px;gap:4px}
.ig.n6 .ig-ic{width:34px;height:34px;border-radius:10px}
.ig.n6 .ig-ic svg,.ig.n6 .ig-ic img{width:18px;height:18px}
"""

__all__ = ["BASE_CSS"]
