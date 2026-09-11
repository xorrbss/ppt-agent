import collections
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
backend = root / "servers/fastapi"
files = [
"services/authored_presentation_generation.py", "services/authored_presentation_pipeline.py",
"services/authored_generation_quality_gate.py", "services/authored_checkpoint_state.py",
"services/authored_checkpoint_stage_batch.py", "services/authored_checkpoint_service.py",
"services/authored_checkpoint_html.py", "services/authored_presentation_artifacts.py",
"services/authored_presentation_author_batch.py", "services/authored_illustration_stage.py",
"services/authored_author_stage_rescue.py", "services/authored_vqa_source_integrity.py",
"services/authored_generation_observability.py", "utils/get_env_programme.py", "utils/renderer_markup_policy.py",
"utils/llm_calls/author_vision_qa.py", "utils/llm_calls/author_slide.py", "utils/llm_calls/author_batch.py",
]
results = []
for name in files:
    baseline = subprocess.run(["git", "show", "HEAD:servers/fastapi/" + name], cwd=root, capture_output=True, check=True).stdout
    command = [sys.executable, "-m", "ruff", "check", "--output-format=json", "--stdin-filename", name, "-"]
    old = json.loads(subprocess.run(command, cwd=backend, input=baseline, capture_output=True).stdout)
    new = json.loads(subprocess.run(command, cwd=backend, input=(backend/name).read_bytes(), capture_output=True).stdout)
    counts = lambda rows: collections.Counter((row["code"], row["message"]) for row in rows)
    added = counts(new) - counts(old)
    results.append({"file": name, "baseline": len(old), "current": len(new),
                    "introduced": [{"code": key[0], "message": key[1], "count": count} for key, count in added.items()]})
output = root / "artifacts/verbatim-design-impl-20260911/T2-ruff-baseline.json"
output.write_text(json.dumps(results, indent=2), encoding="utf-8")
print(json.dumps(results, indent=2))
