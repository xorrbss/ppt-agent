"""Data archetypes: comparison, before_after, kpi, gauge, matrix.

comparison and before_after are real ``<table>`` elements so the hybrid
exporter can promote them to a native PowerPoint table; kpi tiles are plain
DOM text and shape, which the exporter already promotes one by one.
"""

from __future__ import annotations

TABLE_CSS = """\
.ig-compare,.ig-before-after{display:flex;align-items:stretch;justify-content:center}
.ig-table{position:relative;width:100%;height:100%;border-collapse:separate;border-spacing:0;
  table-layout:fixed;font-size:18px;line-height:1.4;background:$white}
.ig-table th,.ig-table td{padding:14px 18px;border-bottom:1px solid $line;text-align:left;vertical-align:middle;
  word-break:keep-all}
.ig-table thead th{font-size:20px;font-weight:800;color:$ink;background:$surface;border-bottom:2px solid $primary_soft}
.ig-table thead th.is-key{background:$primary;color:$on_primary}
.ig-table tbody th{font-weight:700;color:$ink;background:$surface;width:26%}
.ig-table td{font-weight:500;color:$ink}
.ig-table td.is-key{background:$primary_tint;color:$primary_deep;font-weight:700}
.ig-table tr.is-key th,.ig-table tr.is-key td{background:$primary_tint;color:$primary_deep;font-weight:700}
.ig-table .ig-th-sub{display:block;font-size:14px;font-weight:500;color:$muted;margin-top:2px}
.ig-table td.ig-td-num{font-size:26px;font-weight:800;color:$primary_deep;text-align:right}
.ig-table td.ig-td-after{background:$primary_tint;color:$primary_deep;font-weight:800;font-size:22px;text-align:right}
.ig-table td.ig-td-before{color:$muted;text-align:right;font-size:20px}
.ig-table td.ig-td-arrow{width:56px;text-align:center;color:$primary;font-weight:800;font-size:20px}
.ig-table td.ig-td-value{font-size:22px;font-weight:700;color:$ink;text-align:right}
.ig-table th.ig-th-delta{width:16%;text-align:right}
.ig-table td.ig-td-delta{width:16%;font-size:16px;font-weight:700;color:$primary;text-align:right}
.ig.n3 .ig-table{font-size:20px}
.ig.n3 .ig-table th,.ig.n3 .ig-table td{padding:20px 22px}
.ig.n6 .ig-table{font-size:16px}
.ig.n6 .ig-table th,.ig.n6 .ig-table td{padding:9px 14px}
"""

KPI_CSS = """\
.ig-kpi{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:20px;align-items:stretch;
  align-content:stretch}
.ig-kpi .ig-node{padding:26px 20px;gap:10px;justify-content:center}
.ig-kpi .ig-node-bg{background:$surface;box-shadow:none}
.ig-kpi .ig-num{font-size:56px}
.ig-kpi .ig-node.is-key .ig-node-bg{background:$primary;border:1px solid $primary;box-shadow:0 18px 36px $primary_glow}
.ig-kpi .ig-node.is-key .ig-num{font-size:64px}
.ig.n5 .ig-kpi .ig-num,.ig-kpi.n5 .ig-num{font-size:44px}
.ig.n6 .ig-kpi .ig-num,.ig-kpi.n6 .ig-num{font-size:38px}
"""

GAUGE_CSS = """\
.ig-gauge{display:grid;grid-template-columns:360px 1fr;align-items:stretch;column-gap:48px}
.ig-gauge .ig-dial{position:relative;width:320px;height:320px;margin:auto}
.ig-gauge .ig-dial-ring{position:absolute;left:0;top:0;width:320px;height:320px;border-radius:160px;
  background:conic-gradient($primary 0deg 0deg,$primary_tint 0deg 360deg)}
.ig-gauge .ig-dial-ring.v10{background:conic-gradient($primary 0deg 36deg,$primary_tint 36deg 360deg)}
.ig-gauge .ig-dial-ring.v20{background:conic-gradient($primary 0deg 72deg,$primary_tint 72deg 360deg)}
.ig-gauge .ig-dial-ring.v30{background:conic-gradient($primary 0deg 108deg,$primary_tint 108deg 360deg)}
.ig-gauge .ig-dial-ring.v40{background:conic-gradient($primary 0deg 144deg,$primary_tint 144deg 360deg)}
.ig-gauge .ig-dial-ring.v50{background:conic-gradient($primary 0deg 180deg,$primary_tint 180deg 360deg)}
.ig-gauge .ig-dial-ring.v60{background:conic-gradient($primary 0deg 216deg,$primary_tint 216deg 360deg)}
.ig-gauge .ig-dial-ring.v70{background:conic-gradient($primary 0deg 252deg,$primary_tint 252deg 360deg)}
.ig-gauge .ig-dial-ring.v80{background:conic-gradient($primary 0deg 288deg,$primary_tint 288deg 360deg)}
.ig-gauge .ig-dial-ring.v90{background:conic-gradient($primary 0deg 324deg,$primary_tint 324deg 360deg)}
.ig-gauge .ig-dial-ring.v100{background:$primary}
.ig-gauge .ig-dial-hole{position:absolute;left:34px;top:34px;width:252px;height:252px;border-radius:126px;
  background:$white;box-shadow:0 8px 24px rgba(15,23,42,0.08)}
.ig-gauge .ig-dial .ig-node{position:absolute;left:34px;top:34px;width:252px;height:252px;justify-content:center;
  padding:20px}
.ig-gauge .ig-dial .ig-node-bg{display:none}
.ig-gauge .ig-dial .ig-num{font-size:60px}
.ig-gauge .ig-facts{display:flex;flex-direction:column;gap:12px;justify-content:stretch}
.ig-gauge .ig-facts .ig-node{flex:1 1 0;flex-direction:row;align-items:center;text-align:left;
  gap:16px;padding:12px 18px}
.ig-gauge .ig-facts .ig-node-bg{border-radius:12px;box-shadow:none;background:$surface}
.ig-gauge .ig-facts .ig-text{position:relative;display:flex;flex-direction:column;gap:2px;flex:1 1 0}
.ig-gauge .ig-facts .ig-tail{position:relative;flex:none}
.ig-gauge .ig-facts .ig-num{font-size:28px}
.ig-gauge .ig-facts .ig-node.is-key .ig-node-bg{background:$primary;border:1px solid $primary}
.ig-gauge .ig-dial .ig-node.is-key .ig-label,.ig-gauge .ig-dial .ig-node.is-key .ig-num{color:$primary_deep}
.ig-gauge .ig-dial .ig-node.is-key .ig-desc,.ig-gauge .ig-dial .ig-node.is-key .ig-unit{color:$muted}
"""

MATRIX_CSS = """\
.ig-matrix{display:grid;grid-template-columns:44px 1fr 1fr;grid-template-rows:1fr 1fr 32px;column-gap:0;row-gap:0}
.ig-matrix .ig-ax-y{grid-column:1;grid-row:1;align-self:start;font-size:14px;font-weight:800;color:$ink;
  line-height:18px;padding-top:4px;padding-right:6px;text-align:right}
.ig-matrix .ig-ax-y-low{grid-column:1;grid-row:2;align-self:end;font-size:13px;font-weight:700;color:$muted;
  padding-right:6px;text-align:right;padding-bottom:6px}
.ig-matrix .ig-ax-x{grid-column:3;grid-row:3;align-self:end;justify-self:end;font-size:14px;font-weight:800;
  color:$ink;line-height:18px;padding-top:8px}
.ig-matrix .ig-ax-x-low{grid-column:2;grid-row:3;align-self:end;justify-self:start;font-size:13px;font-weight:700;
  color:$muted;padding-top:8px}
.ig-q{position:relative;display:flex;flex-direction:column;gap:10px;padding:34px 16px 14px;min-height:150px}
.ig-q-bg{position:absolute;left:0;top:0;width:100%;height:100%;background:$white;border:1px solid $line}
.ig-q.q-tl{grid-column:2;grid-row:1}
.ig-q.q-tr{grid-column:3;grid-row:1}
.ig-q.q-bl{grid-column:2;grid-row:2}
.ig-q.q-br{grid-column:3;grid-row:2}
.ig-q.q-tl .ig-q-bg{background:$surface;border-radius:14px}
.ig-q.q-tr .ig-q-bg{background:$primary_tint;border:1px solid $primary_soft;border-radius:14px}
.ig-q.q-bl .ig-q-bg{border-radius:14px}
.ig-q.q-br .ig-q-bg{background:$surface;border-radius:14px}
.ig-qtag{position:absolute;left:16px;top:10px;font-size:13px;font-weight:800;color:$muted;line-height:18px}
.ig-q.q-tr .ig-qtag{color:$primary_deep}
.ig-matrix .ig-node{flex-direction:row;align-items:center;text-align:left;gap:14px;padding:10px 14px}
.ig-matrix .ig-node-bg{border-radius:12px}
.ig-matrix .ig-text{position:relative;display:flex;flex-direction:column;gap:2px;min-width:0}
.ig-matrix .ig-label{font-size:18px}
.ig-matrix .ig-desc{font-size:13px}
.ig-matrix .ig-ic{width:36px;height:36px}
.ig-matrix .ig-ic svg,.ig-matrix .ig-ic img{width:20px;height:20px}
"""

DATA_CSS = TABLE_CSS + KPI_CSS + GAUGE_CSS + MATRIX_CSS

__all__ = ["DATA_CSS"]
