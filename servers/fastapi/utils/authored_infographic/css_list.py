"""List and scene archetypes: icon_list, checklist, scene.

The scene root reserves ``.ig-art`` for the one picture the deck may spend on
this slide (the illustration slot today, a generated object cutout once the
icon pack stage lands) and lets the model place each callout with the only
inline style the contract admits: ``left``/``top``/``width``/``height`` in px.
"""

from __future__ import annotations

ICON_LIST_CSS = """\
.ig-icon-list{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:20px;align-items:stretch;
  align-content:stretch}
.ig-icon-list .ig-node{padding:26px 18px;gap:12px;justify-content:center}
.ig-icon-list .ig-ic{width:64px;height:64px;border-radius:18px}
.ig-icon-list .ig-ic svg,.ig-icon-list .ig-ic img{width:32px;height:32px}
.ig.n5 .ig-icon-list .ig-ic,.ig-icon-list.n5 .ig-ic{width:52px;height:52px}
.ig.n6 .ig-icon-list .ig-ic,.ig-icon-list.n6 .ig-ic{width:44px;height:44px;border-radius:12px}
.ig.n6 .ig-icon-list .ig-ic svg,.ig-icon-list.n6 .ig-ic svg,.ig.n6 .ig-icon-list .ig-ic img,
  .ig-icon-list.n6 .ig-ic img{width:22px;height:22px}
"""

CHECKLIST_CSS = """\
.ig-checklist{display:flex;flex-direction:column;justify-content:stretch;gap:10px;padding:0 4%}
.ig-checklist .ig-node{flex:1 1 0;flex-direction:row;align-items:center;text-align:left;gap:18px;
  padding:12px 22px}
.ig-checklist .ig-node-bg{border-radius:12px;box-shadow:none;background:$surface}
.ig-checklist .ig-node.is-key .ig-node-bg{background:$primary_tint;border:1px solid $primary_soft;box-shadow:none}
.ig-checklist .ig-node.is-key .ig-label{color:$primary_deep}
.ig-checklist .ig-node.is-key .ig-desc,.ig-checklist .ig-node.is-key .ig-unit{color:$muted}
.ig-checklist .ig-node.is-key .ig-num{color:$primary_deep}
.ig-checklist .ig-check{position:relative;flex:none;width:32px;height:32px;border-radius:16px;background:$white;
  border:2px solid $line}
.ig-checklist .ig-check-mark{position:absolute;left:10px;top:5px;width:9px;height:16px;
  border-right:3px solid $primary;border-bottom:3px solid $primary;transform:rotate(45deg)}
.ig-checklist .ig-node.is-done .ig-check{background:$primary;border:2px solid $primary}
.ig-checklist .ig-node.is-done .ig-check-mark{border-right:3px solid $white;border-bottom:3px solid $white}
.ig-checklist .ig-node.is-open .ig-check-mark{display:none}
.ig-checklist .ig-text{position:relative;display:flex;flex-direction:column;gap:2px;flex:1 1 0}
.ig-checklist .ig-tail{position:relative;flex:none;text-align:right}
"""

SCENE_CSS = """\
.ig-scene{min-height:420px}
.ig-scene .ig-art{position:absolute;left:50%;top:50%;width:720px;height:405px;margin-left:-360px;margin-top:-202px;
  display:flex;align-items:center;justify-content:center}
.ig-scene .ig-art img{display:block;max-width:100%;max-height:100%}
.ig-scene .ig-co{position:absolute;width:260px;height:88px;padding:12px 14px 12px 52px;display:flex;
  flex-direction:column;justify-content:center;gap:3px;text-align:left}
.ig-scene .ig-co-bg{position:absolute;left:0;top:0;width:100%;height:100%;border-radius:14px;background:$white;
  border:1px solid $line;box-shadow:0 10px 24px rgba(15,23,42,0.10),0 2px 4px rgba(15,23,42,0.06)}
.ig-scene .ig-co .ig-no{position:absolute;left:14px;top:50%;margin-top:-14px;width:28px;height:28px;
  border-radius:14px;background:$primary;color:$on_primary;line-height:28px;text-align:center;font-size:14px}
.ig-scene .ig-co .ig-label{font-size:19px;line-height:1.25}
.ig-scene .ig-co .ig-desc{font-size:13px;line-height:1.35}
.ig-scene .ig-co.is-key .ig-co-bg{background:$primary;border:1px solid $primary;box-shadow:0 14px 30px $primary_glow}
.ig-scene .ig-co.is-key .ig-no{background:$white;color:$primary_deep}
.ig-scene .ig-co.is-key .ig-label{color:$on_primary}
.ig-scene .ig-co.is-key .ig-desc{color:$on_primary_muted}
.ig-scene .ig-pin{position:absolute;width:16px;height:16px;border-radius:8px;background:$primary;
  border:3px solid $white;box-shadow:0 0 0 2px $primary_soft}
"""

LIST_CSS = ICON_LIST_CSS + CHECKLIST_CSS + SCENE_CSS

__all__ = ["LIST_CSS"]
