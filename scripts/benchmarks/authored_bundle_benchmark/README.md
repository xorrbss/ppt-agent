# Authored render/checkpoint benchmark

This provider-free harness measures the product's atomic HTML-to-PNG plus DOM
metrics path and durable slide checkpoint store for 33 and 80 unique slides. It
also runs two concurrent eight-slide decks and exercises pre-seal preservation,
post-seal TTL reclamation, byte-budget reclamation, and time-shifted long-TTL
retention in isolated temporary storage.

Run exactly this command from the repository root:

```powershell
uv run --project servers/fastapi python scripts/benchmarks/authored_bundle_benchmark/run.py
```

The default output is
`artifacts/authored-performance-20260828/evidence.json`. The JSON separates
actual measurements, time-shifted instrumentation, and explicitly unverified
API/production-isolation/PPTX/PDF/long-soak scope.
