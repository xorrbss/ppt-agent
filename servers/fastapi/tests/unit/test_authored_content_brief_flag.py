"""Focused contracts for the authored content-brief rollout flag."""



from utils.get_env_programme import is_authored_content_brief_enabled


def test_authored_content_brief_flag_defaults_on_and_accepts_false(monkeypatch):
    monkeypatch.delenv("AUTHORED_CONTENT_BRIEF_ENABLED", raising=False)
    assert is_authored_content_brief_enabled() is True

    monkeypatch.setenv("AUTHORED_CONTENT_BRIEF_ENABLED", "false")
    assert is_authored_content_brief_enabled() is False

