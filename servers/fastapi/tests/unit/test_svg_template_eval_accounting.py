"""Provider-free evaluation driver persistence and accounting contracts."""
import copy
import importlib.util
import json
from argparse import Namespace
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval" / "run_eval.py"
_spec = importlib.util.spec_from_file_location("svg_eval_accounting_runner", _PATH)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def args(tmp_path, **overrides):
    return Namespace(**{"label": "synthetic", "out": str(tmp_path), "runs": 1, "timeout": 1,
                        "identity_manifest": None, "pptx": False, "judge_renders": None,
                        "judge_references": None, "judge_anchor_high": None, "judge_anchor_low": None,
                        "inputs": "fiction", "templates": [], "dry_run": False, "resume": True,
                        "retry_failed": False, "dsn": "not-used", "app_dir": None, "allow_no_pptx": False,
                        "after": None, "after_dsn": None, **overrides})


def test_uncertain_post_is_durable_and_stops_without_retry(tmp_path, monkeypatch):
    saved = []

    def dispatch(*a, **kw):
        assert saved[-1][0]["state"] == "dispatching"
        kw["on_task"]("task-fiction")
        assert saved[-1][0]["task_id"] == "task-fiction"
        raise TimeoutError("private response body must never be recorded")

    monkeypatch.setattr(runner, "_generate", dispatch)
    records = runner._sample_runs(None, None, None, args(tmp_path, runs=3), {"n_slides": 4}, None, "cell",
                                  on_run=lambda records: saved.append(copy.deepcopy(records)))
    assert len(records) == 1 and records[0]["completed"] is None
    assert records[0]["state"] == "outcome_unknown" and records[0]["task_id"] == "task-fiction"
    assert "private" not in json.dumps(records)
    assert runner._resume_plan(records, ["cell"], 3, True) == {}


def test_generation_completion_survives_measurement_exception(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(runner, "_generate", lambda *a, **kw:
                        ({"data": {"presentation_id": "fiction"}}, "completed", 2))

    def measure(*a, **kw):
        assert saved[-1][0]["completed"] is True
        raise ValueError("private chart content")

    monkeypatch.setattr(runner, "_deck_record", measure)
    records = runner._sample_runs(None, None, None, args(tmp_path), {}, None, "cell",
                                  on_run=lambda records: saved.append(copy.deepcopy(records)))
    assert records[0]["completed"] is True
    assert records[0]["measurement_status"] == "failed"
    assert "private" not in json.dumps(records)


@pytest.mark.parametrize("status,completed,count", [(422, False, 2), (502, None, 1), (408, None, 1)])
def test_http_uncertainty_stops_without_failed_retry_authorization(tmp_path, status, completed, count):
    class Client:
        def post(self, *a, **kw):
            return Namespace(status_code=status)

    records = runner._sample_runs(Client(), None, None, args(tmp_path, runs=2), {}, None, "cell")
    assert len(records) == count
    assert all(item["completed"] is completed for item in records)


def test_explicit_failed_retry_keeps_lineage_and_normal_resume_does_not_retry(tmp_path, monkeypatch):
    import request_builder

    payload = {"content": "fictional", "n_slides": 4, "svgTemplatePin": {"template_id": "fiction"}}
    cell = {"cell": "cell", "input": "fiction", "template": "fiction", "payload": payload, "spec": {}}
    monkeypatch.setattr(request_builder, "build_cells", lambda *a: [cell])
    monkeypatch.setattr(request_builder, "input_source_text", lambda *a: "fictional")
    monkeypatch.setattr(runner, "_db", lambda *a: None)
    monkeypatch.setattr(runner, "_client", lambda *a: nullcontext(None))
    failure = {"run": 1, "cell": "cell", "attempt_id": "failed-first", "completed": False}
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"label": "synthetic", "runs": [failure]}))
    calls = []

    def generate(*a, **kw):
        calls.append(1)
        return {"data": {"presentation_id": "done"}}, "completed", 1

    monkeypatch.setattr(runner, "_generate", generate)
    monkeypatch.setattr(runner, "_deck_record", lambda *a, **kw: {"pages": 4, "completed": True})
    runner.matrix(args(tmp_path))
    assert not calls
    runner.matrix(args(tmp_path, retry_failed=True))
    result = json.loads(path.read_text())
    assert result["runs"][0] == failure
    assert result["runs"][1]["retry_of"] == "failed-first" and result["runs"][1]["run"] == 2
    assert result["accounting"]["attempted_runs"] == 2 and result["accounting"]["completion_rate"] == .5
    runner.matrix(args(tmp_path, retry_failed=True))
    assert len(calls) == 1


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def execute(self, *a):
        pass

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return Cursor(self.rows)


def test_recover_retains_failures_missing_exports_and_exact_request_matching(tmp_path, monkeypatch):
    import request_builder

    payload = {"content": "fictional", "strategy": "svg", "svgTemplateMode": "template-redesign", "n_slides": 4,
               "svgTemplatePin": {"template_id": "t", "revision": 2, "digest": "sha256:" + "a" * 64}}
    cell = {"cell": "cell", "input": "fiction", "template": "t", "payload": payload, "spec": {}}
    monkeypatch.setattr(request_builder, "build_cells", lambda *a: [cell])
    monkeypatch.setattr(request_builder, "input_source_text", lambda *a: "fictional")
    now = datetime.now(timezone.utc)

    def row(pid, body, state, task=None):
        stored = runner._canonical_request(body)
        stored.update(common_header_mode="free", common_header_cover_exception=False)
        stored["_internal_svg_style_snapshot"] = {"ignored-for-public-request-matching": True}
        return (pid, stored, {"presentation_id": pid}, now, now, now + timedelta(seconds=1),
                task or f"task-{pid}", "published" if state == "completed" else state, state)

    rows = [row("failed", payload, "failed"), row("done", payload, "completed"),
            row(None, payload, "failed", "null-first"), row(None, payload, "failed", "null-second"),
            row("different-size", {**payload, "n_slides": 10}, "completed"),
            row("different-pin", {**payload, "svgTemplatePin": {"template_id": "t", "revision": 1, "digest": "sha256:" + "a" * 64}}, "completed"),
            row("different-canvas", {**payload, "svgCanvas": {"widthEmu": 9144000, "heightEmu": 5143500}}, "completed"),
            row("different-map", {**payload, "svgTemplateFrameMap": {"1": "2"}}, "completed")]
    monkeypatch.setattr(runner, "_db", lambda *a: Connection(rows))
    monkeypatch.setattr(runner, "_pptx_from_store", lambda *a: {})
    measured = []

    def measure(*a, **kw):
        measured.append(a[4]["presentation_id"])
        return {"completed": True, "pages": 4, "pptx_evidence": "unavailable"}

    monkeypatch.setattr(runner, "_deck_record", measure)
    original = {"run": 1, "cell": "cell", "attempt_id": "post-rejected", "completed": False}
    path = tmp_path / "synthetic.json"
    pending = {"run": 2, "cell": "cell", "task_id": "task-done", "presentation_id": "done", "completed": True,
               "measurement_status": "pending", "evaluation_identity": {}}
    path.write_text(json.dumps({"label": "synthetic", "runs": [original, pending]}))
    runner.recover(args(tmp_path, app_dir=tmp_path))
    result = json.loads(path.read_text())
    assert result["runs"][0] == original
    assert len(result["runs"]) == 5
    assert result["accounting"]["failed_runs"] == 4 and result["accounting"]["completion_rate"] == .2
    assert result["runs"][1]["pptx_evidence"] == "unavailable"
    assert result["runs"][1]["evaluation_identity"] == {}
    assert measured == ["done"]
    runner.recover(args(tmp_path, app_dir=tmp_path))
    assert len(json.loads(path.read_text())["runs"]) == 5
    assert measured == ["done"]


def test_empty_checkpoint_is_not_first_draft_acceptance(monkeypatch):
    class Measures(Cursor):
        def __init__(self):
            self.query = ""

        def execute(self, query, *a):
            self.query = query

        def fetchone(self):
            return ({},) if "theme" in self.query else ("job",)

        def fetchall(self):
            if "svg_checkpoint" in self.query:
                return [(0, {"attempts": []}), (1, {"attempts": [{"valid": True}], "quality": {"passed": True}})]
            return [(0, '<svg xmlns="http://www.w3.org/2000/svg"/>'),
                    (1, '<svg xmlns="http://www.w3.org/2000/svg"/>')]

    connection = Connection([])
    monkeypatch.setattr(connection, "cursor", Measures)
    record = runner.measure_deck(connection, "fiction")
    assert record["pages"] == 2 and record["attempt_evidence_pages"] == 1
    assert record["first_try_ok"] is None
    assert "first_try_ok_rate" not in runner.normalized_page_metrics(record)


def test_arbitrary_request_text_is_not_hashed_or_persisted_as_identity(tmp_path):
    identity = runner._condition_identity(args(tmp_path), {"content": "private", "api_key": "private"}, None)
    assert identity == {}
    result = runner._write_result(args(tmp_path), None, [{"completed": False}], 1, quiet=True)
    assert result["schema_version"] == 2 and result["accounting"]["failed_runs"] == 1
    assert result["runner_identity"]["scope"].startswith("evaluation harness only")


def test_result_summary_pools_actual_different_page_denominators(tmp_path):
    runs = [{"completed": True, "pages": 4, "first_try_ok": 2, "fallback_pages": 2},
            {"completed": True, "pages": 10, "first_try_ok": 6, "fallback_pages": 3}]
    result = runner._write_result(args(tmp_path), None, runs, 2, quiet=True)
    assert result["summary"]["fallback_rate"]["mean"] == pytest.approx(5 / 14, abs=1e-6)
    assert result["summary"]["first_try_ok_rate"]["mean"] == pytest.approx(8 / 14, abs=1e-6)
    assert result["summary"]["fallback_rate"]["pages"] == 14


def test_judge_asset_bytes_and_actual_model_bind_identity():
    class Judge:
        high, low, context = [("synthetic", b"high")], [("synthetic", b"low")], []

        def settings(self):
            return {"model": "model", "effort": "low"}

    judge = Judge()
    identity = runner._judge_identity(judge, {"served_models": ["model"], "mixed_models": False})
    judge.high = [("synthetic", b"changed")]
    assert identity != runner._judge_identity(judge, {"served_models": ["model"], "mixed_models": False})
    assert runner._judge_identity(judge, {"served_models": ["fallback"], "mixed_models": True}) is None


def test_export_and_judge_errors_leave_completed_deck_metrics(tmp_path, monkeypatch):
    import judge

    monkeypatch.setattr(runner, "measure_deck", lambda *a:
                        {"_texts": "fiction", "pages": 4, "first_try_ok": 2, "fallback_pages": 1,
                         "pages_with_flow_shapes": 0, "repair_causes": {}})

    def fail(*a, **kw):
        raise ValueError("private")

    monkeypatch.setattr(runner, "_export_parts", fail)
    monkeypatch.setattr(judge, "judge_presentation", fail)
    record = runner._deck_record(object(), None, object(), args(tmp_path, pptx=True),
                                 {"presentation_id": "p"}, "no numbers", "cell", None, 1, 1)
    assert record["completed"] is True and record["first_try_ok_rate"] == .5
    assert record["numbers_kept"] is None and record["pptx_evidence"] == "unavailable"
    assert record["export_error_type"] == record["judge_error_type"] == "ValueError"
    assert record["data_integrity"]["status"] == "unavailable"
    assert "private" not in json.dumps(record)


def test_recover_resumes_measurement_after_initial_pending_persistence_interruption(tmp_path, monkeypatch):
    import request_builder

    payload = {"content": "fictional", "strategy": "svg", "svgTemplateMode": "template-redesign", "n_slides": 4,
               "svgTemplatePin": {"template_id": "t", "revision": 2, "digest": "sha256:" + "a" * 64}}
    cell = {"cell": "cell", "input": "fiction", "template": "t", "payload": payload, "spec": {}}
    monkeypatch.setattr(request_builder, "build_cells", lambda *a: [cell])
    monkeypatch.setattr(request_builder, "input_source_text", lambda *a: "fictional")
    now = datetime.now(timezone.utc)
    rows = [("recovered-deck", runner._canonical_request(payload), {"presentation_id": "recovered-deck"},
             now, now, now + timedelta(seconds=1), "recovered-task", "published", "completed")]
    monkeypatch.setattr(runner, "_db", lambda *a: Connection(rows))
    monkeypatch.setattr(runner, "_make_judge_for", lambda *a: None)
    history = [{"run": 1, "cell": "cell", "attempt_id": "failed-attempt", "task_id": "failed-task",
                "completed": False, "state": "failed", "preserved_marker": "failure-history"},
               {"run": 2, "cell": "cell", "attempt_id": "unknown-attempt", "task_id": "unknown-task",
                "completed": None, "state": "outcome_unknown", "preserved_marker": "unknown-history"}]
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"label": "synthetic", "runs": history}))
    measured = []

    def measure(*a, **kw):
        measured.append(a[4]["presentation_id"])
        return {"completed": True, "pages": 4, "first_try_ok": 3, "fallback_pages": 1}

    monkeypatch.setattr(runner, "_deck_record", measure)
    persist = runner.atomic_write_json

    class InterruptedRecovery(BaseException):
        pass

    def interrupt_after_persist(target, result):
        persist(target, result)
        durable = json.loads(path.read_text())
        recovered = next(record for record in durable["runs"] if record.get("task_id") == "recovered-task")
        assert recovered["completed"] is True
        assert recovered["presentation_id"] == "recovered-deck"
        assert recovered["measurement_status"] == "pending"
        assert not measured
        raise InterruptedRecovery

    monkeypatch.setattr(runner, "atomic_write_json", interrupt_after_persist)
    with pytest.raises(InterruptedRecovery):
        runner.recover(args(tmp_path))
    interrupted = json.loads(path.read_text())
    assert interrupted["runs"][:2] == history
    assert len(interrupted["runs"]) == 3
    assert measured == []

    monkeypatch.setattr(runner, "atomic_write_json", persist)
    runner.recover(args(tmp_path))
    resumed = json.loads(path.read_text())
    assert resumed["runs"][:2] == history
    assert len(resumed["runs"]) == 3
    assert resumed["runs"][2]["attempt_id"] == interrupted["runs"][2]["attempt_id"]
    assert resumed["runs"][2]["run"] == interrupted["runs"][2]["run"]
    assert resumed["runs"][2]["measurement_status"] == "available"
    assert resumed["runs"][2]["pages"] == 4
    assert measured == ["recovered-deck"]

    runner.recover(args(tmp_path))
    assert json.loads(path.read_text())["runs"] == resumed["runs"]
    assert measured == ["recovered-deck"]
