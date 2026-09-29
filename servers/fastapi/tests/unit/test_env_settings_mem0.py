import pytest

from utils.env_settings.mem0 import ENV_NAMES, get_mem0_settings

_NON_ENABLED = tuple(n for n in ENV_NAMES if n != "MEM0_ENABLED")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    s = get_mem0_settings()
    assert s.enabled is True
    assert (s.top_k, s.max_context_chars, s.max_stored_turns) == (8, 6000, 20)
    assert s.presentation_namespace_prefix == "presentation"
    assert s.chat_namespace_prefix == "presentation"
    assert s.spacy_model == "en_core_web_sm"
    assert s.require_spacy_model is True
    assert (s.mem0_dir, s.qdrant_path, s.history_db_path) == (None, None, None)
    assert s.collection_name == "presenton_memories"
    assert s.embedder_provider == "fastembed"
    assert s.embedder_model == "BAAI/bge-small-en-v1.5"
    assert s.embedding_dims == 384
    assert s.llm_model is None
    assert s.llm_api_key == "ollama"
    assert s.llm_base_url is None


def test_string_normalisation_and_blank_is_unset(monkeypatch):
    monkeypatch.setenv("MEM0_COLLECTION_NAME", "  coll  ")
    monkeypatch.setenv("MEM0_EMBEDDER_PROVIDER", "   ")
    monkeypatch.setenv("MEM0_DIR", " /x/mem0 ")
    monkeypatch.setenv("MEM0_LLM_API_KEY", "")
    s = get_mem0_settings()
    assert s.collection_name == "coll"
    assert s.embedder_provider == "fastembed"
    assert s.mem0_dir == "/x/mem0"
    assert s.llm_api_key == "ollama"


def test_chat_prefix_fallback_chain(monkeypatch):
    monkeypatch.setenv("MEM0_PRESENTATION_NAMESPACE_PREFIX", " deck ")
    s = get_mem0_settings()
    assert s.presentation_namespace_prefix == "deck"
    assert s.chat_namespace_prefix == "deck"
    monkeypatch.setenv("MEM0_CHAT_NAMESPACE_PREFIX", "chat")
    s = get_mem0_settings()
    assert s.chat_namespace_prefix == "chat"
    assert s.presentation_namespace_prefix == "deck"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12", 12), (" 5 ", 5), ("0", 1), ("-3", 1), ("abc", 8), ("1.5", 8), ("", 8)],
)
def test_numeric_clamps_to_one_and_malformed_defaults(monkeypatch, raw, expected):
    monkeypatch.setenv("MEM0_TOP_K", raw)
    assert get_mem0_settings().top_k == expected


def test_other_numeric_knobs(monkeypatch):
    monkeypatch.setenv("MEM0_EMBEDDING_DIMS", "768")
    monkeypatch.setenv("CHAT_MAX_STORED_TURNS", "junk")
    monkeypatch.setenv("MEM0_MAX_CONTEXT_CHARS", "0")
    s = get_mem0_settings()
    assert s.embedding_dims == 768
    assert s.max_stored_turns == 20
    assert s.max_context_chars == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("ON", True), ("0", False), ("no", False),
     ("", True), ("garbage", True)],
)
def test_require_spacy_bool_contract(monkeypatch, raw, expected):
    monkeypatch.setenv("MEM0_REQUIRE_SPACY_MODEL", raw)
    assert get_mem0_settings().require_spacy_model is expected


@pytest.mark.parametrize(
    ("raw", "expected"), [("false", False), ("0", False), ("true", True), ("", True)]
)
def test_enabled_uses_runtime_accessor(monkeypatch, raw, expected):
    monkeypatch.setenv("MEM0_ENABLED", raw)
    assert get_mem0_settings().enabled is expected
