from copy import deepcopy

from video_moderation.agents import VideoModerationOrchestrator
from video_moderation.provider import build_doubao_chat_payload, provider_config
from video_moderation.storage import DEFAULT_STATE


def test_video_moderation_detects_domain_violation():
    state = deepcopy(DEFAULT_STATE)
    run = VideoModerationOrchestrator(state).analyze(state["videos"][0])

    assert run["final_verdict"] == "violation"
    violence = next(item for item in run["domain_results"] if item["domain_id"] == "violence")
    assert violence["verdict"] == "violation"
    assert violence["evidence"]["grounding"]
    assert violence["reasoning"][0]["step"] == "collect_signals"


def test_feedback_increments_agent_version_and_adds_learned_keywords():
    state = deepcopy(DEFAULT_STATE)
    orchestrator = VideoModerationOrchestrator(state)

    learning = orchestrator.learn_from_feedback(
        {
            "domain_id": "regulated_goods",
            "correct_label": "violation",
            "notes": "Business badcase: fake id sale should be blocked",
        }
    )

    assert learning["new_version"] == 2
    assert state["agent_versions"]["regulated_goods"] == 2
    assert "fake" in learning["learned_keywords"]


def test_rebuild_subagents_updates_all_versions():
    state = deepcopy(DEFAULT_STATE)
    orchestrator = VideoModerationOrchestrator(state)

    result = orchestrator.rebuild_subagents(reason="test_all")

    assert result["count"] == len(state["risk_domains"])
    assert all(update["new_version"] == 2 for update in result["updated"])
    assert len(state["iteration_log"]) == len(state["risk_domains"])


def test_doubao_payload_uses_ark_chat_completions_shape(monkeypatch):
    monkeypatch.setenv("DOUBAO_VIDEO_INPUT_MODE", "video_url")
    state = deepcopy(DEFAULT_STATE)
    domain = state["risk_domains"][0]
    video = {**state["videos"][0], "source_url": "https://example.com/video.mp4"}

    payload = build_doubao_chat_payload(domain, video, model="ep-test")

    assert payload["model"] == "ep-test"
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["content"][0]["type"] == "text"
    assert payload["messages"][0]["content"][1] == {
        "type": "video_url",
        "video_url": {"url": "https://example.com/video.mp4"},
    }
    assert payload["response_format"] == {"type": "json_object"}


def test_provider_config_uses_doubao_ark_env(monkeypatch):
    monkeypatch.setenv("VIDEO_MODERATION_PROVIDER", "doubao")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.setenv("ARK_MODEL", "ep-test")

    cfg = provider_config()

    assert cfg["provider"] == "doubao"
    assert cfg["ark_model"] == "ep-test"
    assert cfg["ark_api_key_configured"] is True
