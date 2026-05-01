from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx


DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ARK_MODEL = "doubao-1-5-vision-pro-32k-250115"
ENV_FILE = Path(__file__).resolve().parent / ".env"


class ProviderError(RuntimeError):
    pass


def load_local_env() -> None:
    """Load local, git-ignored provider config without overriding process env."""

    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def provider_config() -> dict[str, Any]:
    load_local_env()
    return {
        "provider": os.getenv("VIDEO_MODERATION_PROVIDER", "local"),
        "ark_base_url": os.getenv("ARK_BASE_URL", DEFAULT_ARK_BASE_URL),
        "ark_model": os.getenv("ARK_MODEL", DEFAULT_ARK_MODEL),
        "ark_api_key_configured": bool(_ark_api_key()),
        "doubao_video_input_mode": os.getenv("DOUBAO_VIDEO_INPUT_MODE", "video_url"),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
    }


def build_policy_prompt(domain: dict[str, Any], video: dict[str, Any]) -> str:
    return f"""
You are a video safety moderation subagent.

Risk domain:
- id: {domain["id"]}
- name: {domain["name"]}
- policy keywords: {", ".join(domain.get("keywords", []))}

Return only strict JSON with this schema:
{{
  "verdict": "pass" | "violation",
  "score": number between 0 and 1,
  "confidence": number between 0 and 1,
  "matched_keywords": string[],
  "evidence": {{
    "grounding": array,
    "ocr": string[],
    "transcript": array
  }},
  "reasoning": [
    {{"step": "string", "summary": "string"}}
  ]
}}

Use the provided visual frames or video, transcript, OCR text, and grounding labels.
Prefer explicit visual/text evidence over speculation. If evidence is weak, return pass with low score.

Video metadata:
- id: {video.get("id")}
- title: {video.get("title", "")}
- description: {video.get("description", "")}
- transcript: {video.get("transcript", "")}
- OCR texts: {json.dumps(video.get("ocr_texts", []), ensure_ascii=False)}
- grounding: {json.dumps(video.get("grounding", []), ensure_ascii=False)}
""".strip()


async def analyze_with_doubao(domain: dict[str, Any], video: dict[str, Any], frame_limit: int = 6) -> dict[str, Any]:
    """Analyze one risk domain through Volcengine Ark / Doubao.

    The request follows Ark's OpenAI-compatible Chat Completions shape:
    POST {ARK_BASE_URL}/chat/completions with Authorization: Bearer ARK_API_KEY.
    """

    load_local_env()
    api_key = _ark_api_key()
    if not api_key:
        raise ProviderError("ARK_API_KEY is not configured")

    model = os.getenv("ARK_MODEL", DEFAULT_ARK_MODEL)
    base_url = os.getenv("ARK_BASE_URL", DEFAULT_ARK_BASE_URL).rstrip("/")
    payload = build_doubao_chat_payload(domain, video, model=model, frame_limit=frame_limit)

    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if res.status_code >= 400:
        raise ProviderError(f"Doubao Ark error {res.status_code}: {res.text[:500]}")

    data = res.json()
    text = data["choices"][0]["message"]["content"]
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

    parsed = _parse_json_object(str(text))
    parsed["provider"] = "doubao"
    parsed["model"] = model
    return parsed


def build_doubao_chat_payload(
    domain: dict[str, Any],
    video: dict[str, Any],
    model: str | None = None,
    frame_limit: int = 6,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": build_policy_prompt(domain, video)}]

    if video.get("source_url") and os.getenv("DOUBAO_VIDEO_INPUT_MODE", "video_url") == "video_url":
        content.append({"type": "video_url", "video_url": {"url": video["source_url"]}})
    else:
        for data_url in extract_frame_data_urls(video, frame_limit=frame_limit):
            content.append({"type": "image_url", "image_url": {"url": data_url}})

    if len(content) == 1 and video.get("source_url"):
        content.append(
            {
                "type": "text",
                "text": (
                    "Video URL for reference only. If this model endpoint does not accept video_url, "
                    f"set DOUBAO_VIDEO_INPUT_MODE=frames and install ffmpeg. URL: {video['source_url']}"
                ),
            }
        )

    return {
        "model": model or os.getenv("ARK_MODEL", DEFAULT_ARK_MODEL),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }


def extract_frame_data_urls(video: dict[str, Any], frame_limit: int = 6) -> list[str]:
    path_value = video.get("file_path")
    if not path_value or not shutil.which("ffmpeg"):
        return []

    video_path = Path(path_value)
    if not video_path.exists():
        return []

    frame_dir = video_path.parent / f"{video_path.stem}_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frame_dir / "frame_%03d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1,scale='min(960,iw)':-2",
        "-frames:v",
        str(frame_limit),
        pattern,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    data_urls = []
    for frame in sorted(frame_dir.glob("frame_*.jpg"))[:frame_limit]:
        encoded = base64.b64encode(frame.read_bytes()).decode("ascii")
        data_urls.append(f"data:image/jpeg;base64,{encoded}")
    return data_urls


def _ark_api_key() -> str:
    return (
        os.getenv("ARK_API_KEY")
        or os.getenv("VOLCENGINE_ARK_API_KEY")
        or os.getenv("DOUBAO_API_KEY")
        or ""
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ProviderError(f"Doubao Ark returned non-JSON content: {text[:500]}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Doubao Ark returned malformed JSON content: {text[:500]}") from exc
