import ast
from pathlib import Path

import pytest

from utils.env_parse import EnvValueError
from utils.env_settings import authored_runtime as ar
from utils.env_settings.authored_runtime import ENV_NAMES


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_module_reads_no_env_at_import_and_stays_below_services():
    source = Path(ar.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = node.module if isinstance(node, ast.ImportFrom) else ""
            names = [module] + [alias.name for alias in node.names]
            for name in names:
                assert not (name or "").startswith(("services", "api")), name
    assert "os.getenv" not in source and "os.environ" not in source
    assert len(set(ENV_NAMES)) == len(ENV_NAMES)


# --- test hooks -------------------------------------------------------------


def test_test_failure_hooks_default_off():
    hooks = ar.get_authored_test_failure_hooks()
    assert hooks == ar.AuthoredTestFailureHooks(enabled=False, slide=0, stage="")


@pytest.mark.parametrize("raw", ["1", "true", " TRUE ", "yes", "on"])
def test_test_failure_hooks_opt_in(monkeypatch, raw):
    monkeypatch.setenv("AUTHORED_TEST_FAILURES_ENABLED", raw)
    monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", " 3 ")
    monkeypatch.setenv("AUTHORED_TEST_FAILURE_STAGE", " Author ")
    hooks = ar.get_authored_test_failure_hooks()
    assert (hooks.enabled, hooks.slide, hooks.stage) == (True, 3, "author")


def test_test_failure_hooks_malformed_slide_is_none(monkeypatch):
    monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "two")
    assert ar.get_authored_test_failure_hooks().slide is None
    monkeypatch.setenv("AUTHORED_TEST_FAILURE_SLIDE", "")
    assert ar.get_authored_test_failure_hooks().slide == 0


# --- storage / retention -----------------------------------------------------


def test_directories_blank_is_unset(monkeypatch):
    assert ar.get_authored_checkpoint_directory() is None
    assert ar.get_authored_quality_receipt_directory() is None
    assert ar.get_fastembed_icon_cache_dir() is None
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", "  ")
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", " /r ")
    monkeypatch.setenv("PRESENTON_FASTEMBED_ICON_CACHE_DIR", " /c ")
    assert ar.get_authored_checkpoint_directory() is None
    assert ar.get_authored_quality_receipt_directory() == "/r"
    assert ar.get_fastembed_icon_cache_dir() == "/c"


def test_evidence_retention_defaults_and_clamp(monkeypatch):
    retention = ar.get_authored_evidence_retention_settings()
    assert retention.ttl_seconds == 86400.0
    assert retention.byte_budget == 512 * 1024 * 1024
    monkeypatch.setenv("AUTHORED_EVIDENCE_RETENTION_TTL_SECONDS", "-5")
    monkeypatch.setenv("AUTHORED_EVIDENCE_RETENTION_BYTE_BUDGET", "-1")
    retention = ar.get_authored_evidence_retention_settings()
    assert (retention.ttl_seconds, retention.byte_budget) == (0.0, 0)
    monkeypatch.setenv("AUTHORED_EVIDENCE_RETENTION_TTL_SECONDS", "abc")
    monkeypatch.setenv("AUTHORED_EVIDENCE_RETENTION_BYTE_BUDGET", "1.5")
    retention = ar.get_authored_evidence_retention_settings()
    assert retention.ttl_seconds == 86400.0
    assert retention.byte_budget == 512 * 1024 * 1024


def test_dom_metrics_budget_none_unset_raises_malformed(monkeypatch):
    assert ar.get_authored_dom_metrics_cache_byte_budget() is None
    monkeypatch.setenv("AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET", "1024")
    assert ar.get_authored_dom_metrics_cache_byte_budget() == 1024
    for raw in ("-1", "x"):
        monkeypatch.setenv("AUTHORED_DOM_METRICS_CACHE_BYTE_BUDGET", raw)
        with pytest.raises(EnvValueError):
            ar.get_authored_dom_metrics_cache_byte_budget()


# --- pipeline knobs ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 8), ("3", 3), ("0", 1), ("-2", 1), ("x", 8)]
)
def test_slide_pipeline_concurrency(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", raw)
    assert ar.get_authored_slide_pipeline_concurrency() == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "public"),
        (" Local-Development ", "local-development"),
        ("unit-test", "unit-test"),
        ("staging", "public"),
    ],
)
def test_quality_receipt_v3_profile(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_V3_PROFILE", raw)
    assert ar.get_authored_quality_receipt_v3_profile() == expected


def test_powerpoint_qa_interactive_is_opt_in(monkeypatch):
    assert ar.is_authored_powerpoint_qa_interactive_observation_enabled() is False
    monkeypatch.setenv("AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION", "TRUE")
    assert ar.is_authored_powerpoint_qa_interactive_observation_enabled() is True
    monkeypatch.setenv("AUTHORED_POWERPOINT_QA_INTERACTIVE_OBSERVATION", "maybe")
    assert ar.is_authored_powerpoint_qa_interactive_observation_enabled() is False


def test_semantic_dual_output_mode_raw(monkeypatch):
    assert ar.get_authored_semantic_dual_output_mode() == "shadow"
    monkeypatch.setenv("AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE", " Declared ")
    assert ar.get_authored_semantic_dual_output_mode() == "declared"
    monkeypatch.setenv("AUTHORED_SEMANTIC_DUAL_OUTPUT_MODE", "bogus")
    assert ar.get_authored_semantic_dual_output_mode() == "bogus"


def test_reasoning_effort_raw_text(monkeypatch):
    assert ar.get_authored_reasoning_effort() == ""
    assert ar.get_codex_reasoning_effort() is None
    monkeypatch.setenv("AUTHORED_REASONING_EFFORT", " High ")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "low")
    assert ar.get_authored_reasoning_effort() == " High "
    assert ar.get_codex_reasoning_effort() == "low"


def test_exact_reconstruction_settings(monkeypatch):
    configured = ar.get_authored_exact_reconstruction_settings()
    assert configured == ar.AuthoredExactReconstructionSettings(False, ())
    monkeypatch.setenv("AUTHORED_EXACT_RECONSTRUCTION_ENABLED", "yes")
    monkeypatch.setenv("AUTHORED_EXACT_STYLE_IDS", " a, ,b ,, c-9 ")
    configured = ar.get_authored_exact_reconstruction_settings()
    assert configured.enabled is True
    assert configured.style_ids == ("a", "b", "c-9")


# --- renderer boundary -------------------------------------------------------


def test_renderer_settings_defaults(monkeypatch):
    renderer = ar.get_authored_renderer_settings()
    assert renderer.mode == "isolated"
    assert renderer.deployment_tier == "local"
    assert renderer.worker_command_json == ""
    assert renderer.os_isolation == ""
    monkeypatch.setenv("AUTHORED_RENDERER_MODE", " Legacy ")
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "Production")
    monkeypatch.setenv("AUTHORED_RENDERER_WORKER_COMMAND_JSON", ' ["x"] ')
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", " a ")
    renderer = ar.get_authored_renderer_settings()
    assert renderer == ar.AuthoredRendererSettings("legacy", "production", '["x"]', "a")


def test_renderer_flag_set_or_default(monkeypatch):
    name = "AUTHORED_RENDERER_KILL_SWITCH"
    assert ar.renderer_flag(name) is False
    assert ar.renderer_flag(name, default=True) is True
    monkeypatch.setenv(name, "")
    assert ar.renderer_flag(name, default=True) is False
    monkeypatch.setenv(name, " On ")
    assert ar.renderer_flag(name) is True
    with pytest.raises(ValueError):
        ar.renderer_flag("AUTHORED_RENDERER_MODE")


def test_renderer_worker_marker(monkeypatch):
    assert ar.is_authored_renderer_worker_process() is False
    monkeypatch.setenv("AUTHORED_RENDERER_WORKER", "1")
    assert ar.is_authored_renderer_worker_process() is True
    monkeypatch.setenv("AUTHORED_RENDERER_WORKER", "2")
    assert ar.is_authored_renderer_worker_process() is False


# --- slidespec ----------------------------------------------------------------


def test_repair_proposal_llm_default_on_falsy_disables(monkeypatch):
    assert ar.is_slidespec_repair_proposal_llm_enabled() is True
    monkeypatch.setenv("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", "garbage")
    assert ar.is_slidespec_repair_proposal_llm_enabled() is True
    monkeypatch.setenv("SLIDESPEC_REPAIR_PROPOSAL_LLM_ENABLED", " Off ")
    assert ar.is_slidespec_repair_proposal_llm_enabled() is False


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 3), ("5", 5), ("0", 1), ("99", 8), ("x", 3)]
)
def test_slidespec_slide_concurrency(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("SLIDESPEC_SLIDE_CONCURRENCY", raw)
    assert ar.get_slidespec_slide_concurrency() == expected


def test_rollout_defaults():
    rollout = ar.get_slidespec_rollout_settings()
    assert rollout == ar.SlidespecRolloutSettings(
        import_shadow=False,
        repair_shadow=False,
        repair_apply=True,
        repair_all_slides=True,
        kill_switch=False,
        canary_basis_points=10_000,
        rollout_salt="v1",
    )


def test_rollout_flags_blank_is_off_and_salt_verbatim(monkeypatch):
    monkeypatch.setenv("SLIDESPEC_REPAIR_APPLY", "")
    monkeypatch.setenv("SLIDESPEC_KILL_SWITCH", " yes ")
    monkeypatch.setenv("SLIDESPEC_REPAIR_ROLLOUT_SALT", "")
    rollout = ar.get_slidespec_rollout_settings()
    assert rollout.repair_apply is False
    assert rollout.kill_switch is True
    assert rollout.rollout_salt == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", 10_000), ("100", 10_000), ("12.5", 1250), ("250", 10_000),
     ("-3", 0), ("abc", 0), ("", 0)],
)
def test_rollout_canary_basis_points(monkeypatch, raw, expected):
    monkeypatch.setenv("SLIDESPEC_REPAIR_CANARY", raw)
    assert ar.get_slidespec_rollout_settings().canary_basis_points == expected


# --- raster -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("1", True), ("0", False), ("true", True), ("off", False), ("2", None)],
)
def test_raster_strict_page_errors_tri_state(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv("RASTER_STRICT_PAGE_ERRORS", raw)
    assert ar.get_raster_strict_page_errors() is expected


def test_raster_process_workers(monkeypatch):
    assert ar.get_raster_analysis_process_workers(default=0) == 0
    monkeypatch.setenv("RASTER_ANALYSIS_PROCESS_WORKERS", "2")
    assert ar.get_raster_analysis_process_workers(default=0) == 2
    monkeypatch.setenv("RASTER_ANALYSIS_PROCESS_WORKERS", "-1")
    assert ar.get_raster_analysis_process_workers(default=0) == -1


# --- compatibility ----------------------------------------------------------


def test_compatibility_retirement_flags(monkeypatch):
    flags = ar.get_compatibility_retirement_flags()
    assert flags == ar.CompatibilityRetirementFlags(False, False, False)
    monkeypatch.setenv("LEGACY_GENERATION_MODE_READERS_DISABLED", "1")
    flags = ar.get_compatibility_retirement_flags()
    assert flags.legacy_generation_mode_readers_disabled is True
    assert flags.legacy_image_path_callers_disabled is False


def test_compatibility_archive_signing(monkeypatch):
    signing = ar.get_compatibility_archive_signing()
    assert signing == ar.CompatibilityArchiveSigning(b"", "compatibility-v1")
    monkeypatch.setenv("COMPATIBILITY_ARCHIVE_SIGNING_KEY", "s3cret")
    monkeypatch.setenv("COMPATIBILITY_ARCHIVE_SIGNING_KEY_ID", "  ")
    signing = ar.get_compatibility_archive_signing()
    assert signing == ar.CompatibilityArchiveSigning(b"s3cret", "compatibility-v1")
