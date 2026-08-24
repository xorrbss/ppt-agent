# Authored visual parity evidence

`visual_parity_gate.py` is a fail-closed verifier for a fresh, real-product
evidence bundle. It requires three distinct exports (`authored`, `fidelity`,
`hybrid`), database-resolved generation lineage, PPTX hashes, PowerPoint first
open/reopen render hashes, per-slide diff policy results, zero defect counters,
manual review ledger entries, and the complete KO/ZH/JA plus archetype registry.
It writes `status: passed` only when every required artifact and binding is
present and valid; `promotionReady` remains `false`.

The release matrix fixture is two fresh eight-slide product seeds. Run
`release_matrix_fixture.py` with the existing `seed-authored-semantic-vertical-slice.py`
and real font/database arguments to create the presentation IDs, operation
IDs, generation attempts, and seed receipts consumed by the export stage. The
receipt is explicitly `seeded-not-verified` until actual UI/export/PowerPoint
evidence is supplied. Each receipt also carries a digest-bound semantic proof
with the slide HTML and semantic source. The matrix runner derives languages
and archetypes from that proof and rejects two variants that are only cloned
declarations.
