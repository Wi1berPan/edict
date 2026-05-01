from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .agents import utc_now


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "video_moderation"
UPLOAD_DIR = DATA_DIR / "uploads"
STATE_FILE = DATA_DIR / "state.json"
_LOCK = threading.Lock()


DEFAULT_STATE: dict[str, Any] = {
    "risk_domains": [
        {
            "id": "violence",
            "name": "Violence and weapons",
            "subagent_id": "risk_violence_agent",
            "threshold": 0.46,
            "keywords": ["weapon", "knife", "gun", "blood", "attack", "fight", "violence", "爆炸", "刀", "枪"],
        },
        {
            "id": "adult",
            "name": "Adult and sexual content",
            "subagent_id": "risk_adult_agent",
            "threshold": 0.5,
            "keywords": ["nude", "sex", "porn", "explicit", "underwear", "裸露", "色情"],
        },
        {
            "id": "self_harm",
            "name": "Self-harm",
            "subagent_id": "risk_self_harm_agent",
            "threshold": 0.42,
            "keywords": ["suicide", "self harm", "cutting", "overdose", "自杀", "自残"],
        },
        {
            "id": "hate",
            "name": "Hate and harassment",
            "subagent_id": "risk_hate_agent",
            "threshold": 0.48,
            "keywords": ["hate", "slur", "harass", "racist", "辱骂", "仇恨"],
        },
        {
            "id": "regulated_goods",
            "name": "Regulated goods",
            "subagent_id": "risk_regulated_goods_agent",
            "threshold": 0.44,
            "keywords": ["drug", "cocaine", "gambling", "fake id", "毒品", "赌博"],
        },
    ],
    "agent_versions": {},
    "videos": [
        {
            "id": "vid_demo_001",
            "title": "Street fight clip with visible knife",
            "description": "Demo input for local development.",
            "transcript": "Two people start a fight and someone shouts that there is a knife.",
            "ocr_texts": ["BREAKING NEWS", "knife attack downtown"],
            "grounding": [
                {"frame": "00:00:02.000", "label": "two people fighting", "confidence": 0.88, "box": [0.22, 0.2, 0.56, 0.72]},
                {"frame": "00:00:04.000", "label": "knife in hand", "confidence": 0.76, "box": [0.48, 0.34, 0.58, 0.49]},
            ],
            "created_at": utc_now(),
        },
        {
            "id": "vid_demo_002",
            "title": "Cooking tutorial",
            "description": "A normal cooking video with a kitchen knife used as cookware.",
            "transcript": "Today we cut vegetables and cook soup.",
            "ocr_texts": ["family recipe", "safe cooking"],
            "grounding": [
                {"frame": "00:00:06.000", "label": "chef holding kitchen knife", "confidence": 0.82, "box": [0.39, 0.3, 0.49, 0.52]},
            ],
            "created_at": utc_now(),
        },
    ],
    "runs": [],
    "feedback": [],
    "benchmark_items": [
        {
            "id": "bm_violence_001",
            "video_id": "vid_demo_001",
            "domain_id": "violence",
            "expected_label": "violation",
            "source": "seed",
            "created_at": utc_now(),
        },
        {
            "id": "bm_violence_002",
            "video_id": "vid_demo_002",
            "domain_id": "violence",
            "expected_label": "pass",
            "source": "seed",
            "created_at": utc_now(),
        },
    ],
    "iteration_log": [],
}


def ensure_state() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        save_state(DEFAULT_STATE)


def load_state() -> dict[str, Any]:
    ensure_state()
    with _LOCK:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))


def save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def snapshot_public_state(state: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(state)
    public["summary"] = {
        "domains": len(state.get("risk_domains", [])),
        "videos": len(state.get("videos", [])),
        "runs": len(state.get("runs", [])),
        "feedback": len(state.get("feedback", [])),
        "benchmark_items": len(state.get("benchmark_items", [])),
    }
    return public
