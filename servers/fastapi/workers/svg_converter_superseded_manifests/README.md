# Superseded vendor manifests

Kept bytes of reviewed superseded `vendor/slide_master/MANIFEST.json` files,
named `<sha256>.json` and registered in `workers/public_svg_converter_aliases.py`.

No manifest is registered now. The only entry (`9ab2ba71…`, E3 2026-11-05) was
voided on 2026-11-08: commit `b392c58f7` patched the executed vendored file
`svg_to_pptx/native_objects/chart_data.py` (V5 doughnut hole size), so its
`files` map no longer describes the bytes that execute and, by owner decision D5,
its alias stopped applying. Proofs that pin a converter digest from before that
commit no longer verify and are produced again by a new export.
