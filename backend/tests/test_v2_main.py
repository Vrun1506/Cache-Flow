import pytest
from fastapi.testclient import TestClient

from tutor.v2.main import build_app


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_app_builds_from_env_and_health_reports_the_llm_down(monkeypatch, tmp_path, api):
    monkeypatch.setenv("TUTOR_LLM_API", api)
    monkeypatch.setenv("TUTOR_LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("TUTOR_LOG_DIR", str(tmp_path))
    with TestClient(build_app()) as client:
        body = client.get("/v2/health").json()
        assert body["protocol_version"] == 2
        assert body["components"]["vllm"]["ok"] is False  # the LLM server is unreachable: reported, not hidden
        assert body["components"]["math"]["ok"] is True
        made = client.post("/v2/sessions", json={"preset_id": "quad_5_6", "locale": "en"})
        assert made.status_code == 200 and made.json()["protocol_version"] == 2


def test_replay_file_is_labelled_in_health(monkeypatch, tmp_path):
    store = tmp_path / "demo.jsonl"
    store.write_text("")
    monkeypatch.setenv("TUTOR_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TUTOR_REPLAY_FILE", str(store))
    with TestClient(build_app()) as client:
        detail = client.get("/v2/health").json()["components"]["vllm"]["detail"]
        assert "replayed" in detail
