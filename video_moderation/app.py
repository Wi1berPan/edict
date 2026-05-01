from __future__ import annotations

from pathlib import Path
from typing import Any

import shutil

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import VideoModerationOrchestrator, utc_now
from .provider import provider_config
from .storage import UPLOAD_DIR, load_state, new_id, save_state, snapshot_public_state


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Video Moderation Agent Lab",
    description="Risk-domain subagents with feedback learning, benchmark tracking, and traceable analysis.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class GroundingItem(BaseModel):
    frame: str = "00:00:00.000"
    label: str
    confidence: float = 0.0
    box: list[float] = Field(default_factory=list)


class VideoCreate(BaseModel):
    title: str
    description: str = ""
    transcript: str = ""
    ocr_texts: list[str] = Field(default_factory=list)
    grounding: list[GroundingItem] = Field(default_factory=list)
    source_url: str = ""


class FeedbackCreate(BaseModel):
    domain_id: str
    correct_label: str = Field(pattern="^(pass|violation)$")
    notes: str = ""
    add_to_benchmark: bool = True
    benchmark_domain_id: str = ""
    auto_iterate: bool = True


class RebuildRequest(BaseModel):
    domain_id: str | None = None
    reason: str = "manual"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    data = snapshot_public_state(load_state())
    data["sub_benchmarks"] = _sub_benchmark_summary(data)
    data["iteration_log"] = data.get("iteration_log", [])[:50]
    data["provider_config"] = provider_config()
    return data


@app.get("/api/provider")
async def get_provider():
    return provider_config()


@app.get("/api/risk-domains")
async def risk_domains():
    return {"risk_domains": load_state()["risk_domains"]}


@app.get("/api/videos")
async def videos():
    return {"videos": load_state()["videos"]}


@app.post("/api/videos", status_code=201)
async def create_video(body: VideoCreate):
    state = load_state()
    video = body.model_dump()
    video["id"] = new_id("vid")
    video["created_at"] = utc_now()
    state["videos"].append(video)
    save_state(state)
    return {"ok": True, "video": video}


@app.post("/api/videos/upload", status_code=201)
async def upload_video(request: Request):
    try:
        form = await request.form()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail="Video upload requires python-multipart. Run: pip install -r video_moderation\\requirements.txt",
        ) from exc

    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(status_code=400, detail="Missing video file.")

    title = str(form.get("title") or "")
    description = str(form.get("description") or "")
    transcript = str(form.get("transcript") or "")
    source_url = str(form.get("source_url") or "")
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Please upload a video file.")

    state = load_state()
    video_id = new_id("vid")
    safe_name = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in file.filename or "video.mp4")
    target = UPLOAD_DIR / f"{video_id}_{safe_name}"
    with target.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    video = {
        "id": video_id,
        "title": title or file.filename or video_id,
        "description": description,
        "transcript": transcript,
        "ocr_texts": [],
        "grounding": [],
        "source_url": source_url,
        "file_name": file.filename,
        "file_path": str(target),
        "content_type": file.content_type,
        "created_at": utc_now(),
    }
    state["videos"].insert(0, video)
    save_state(state)
    return {"ok": True, "video": video}


@app.post("/api/videos/{video_id}/analyze")
async def analyze_video(video_id: str, provider: str = "local"):
    state = load_state()
    video = _find(state["videos"], video_id, "video")
    result = await VideoModerationOrchestrator(state).analyze_async(video, provider=provider)
    result["id"] = new_id("run")
    state["runs"].insert(0, result)
    save_state(state)
    return {"ok": True, "run": result}


@app.get("/api/runs")
async def runs(limit: int = 20):
    state = load_state()
    return {"runs": state["runs"][:limit]}


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str):
    state = load_state()
    return {"run": _find(state["runs"], run_id, "run")}


@app.post("/api/runs/{run_id}/feedback")
async def submit_feedback(run_id: str, body: FeedbackCreate):
    state = load_state()
    run = _find(state["runs"], run_id, "run")
    target_domain_id = body.benchmark_domain_id or body.domain_id
    domain = _find(state["risk_domains"], body.domain_id, "risk domain")
    benchmark_domain = _find(state["risk_domains"], target_domain_id, "benchmark risk domain")
    feedback = body.model_dump()
    feedback["benchmark_domain_id"] = target_domain_id
    feedback.update(
        {
            "id": new_id("fb"),
            "run_id": run_id,
            "video_id": run["video_id"],
            "domain_name": domain["name"],
            "benchmark_domain_name": benchmark_domain["name"],
            "created_at": utc_now(),
        }
    )

    orchestrator = VideoModerationOrchestrator(state)
    learning = orchestrator.learn_from_feedback(feedback)
    state["feedback"].insert(0, feedback)

    benchmark_item = None
    if feedback["add_to_benchmark"]:
        benchmark_item = {
            "id": new_id("bm"),
            "video_id": run["video_id"],
            "domain_id": target_domain_id,
            "expected_label": body.correct_label,
            "source": "feedback",
            "feedback_id": feedback["id"],
            "origin_domain_id": body.domain_id,
            "created_at": utc_now(),
        }
        state["benchmark_items"].insert(0, benchmark_item)

    rebuild = None
    if body.auto_iterate:
        rebuild = orchestrator.rebuild_subagents(domain_id=body.domain_id, reason="feedback_badcase")

    save_state(state)
    return {
        "ok": True,
        "feedback": feedback,
        "learning": learning,
        "benchmark_item": benchmark_item,
        "rebuild": rebuild,
    }


@app.get("/api/benchmarks")
async def benchmarks():
    state = load_state()
    return {
        "benchmark_items": state["benchmark_items"],
        "sub_benchmarks": _sub_benchmark_summary(state),
        "agent_versions": state.get("agent_versions", {}),
    }


@app.post("/api/benchmarks/evaluate")
async def evaluate_benchmarks():
    return await evaluate_benchmark_rows(load_state())


@app.get("/api/sub-benchmarks")
async def sub_benchmarks():
    state = load_state()
    return {"sub_benchmarks": _sub_benchmark_summary(state), "iteration_log": state.get("iteration_log", [])[:50]}


@app.post("/api/sub-benchmarks/{domain_id}/evaluate")
async def evaluate_sub_benchmark(domain_id: str):
    state = load_state()
    _find(state["risk_domains"], domain_id, "risk domain")
    result = await evaluate_benchmark_rows(state, domain_id=domain_id)
    return result


@app.post("/api/subagents/rebuild")
async def rebuild_subagents(body: RebuildRequest):
    state = load_state()
    if body.domain_id:
        _find(state["risk_domains"], body.domain_id, "risk domain")
    result = VideoModerationOrchestrator(state).rebuild_subagents(domain_id=body.domain_id, reason=body.reason)
    save_state(state)
    return {"ok": True, **result, "agent_versions": state.get("agent_versions", {})}


async def evaluate_benchmark_rows(state: dict[str, Any], domain_id: str | None = None) -> dict[str, Any]:
    orchestrator = VideoModerationOrchestrator(state)
    rows: list[dict[str, Any]] = []
    items = [
        item for item in state["benchmark_items"]
        if domain_id is None or item["domain_id"] == domain_id
    ]
    for item in items:
        video = _find(state["videos"], item["video_id"], "video")
        run = orchestrator.analyze(video)
        domain_result = next((r for r in run["domain_results"] if r["domain_id"] == item["domain_id"]), None)
        predicted = domain_result["verdict"] if domain_result else "missing"
        rows.append(
            {
                "benchmark_id": item["id"],
                "video_id": item["video_id"],
                "domain_id": item["domain_id"],
                "expected": item["expected_label"],
                "predicted": predicted,
                "passed": predicted == item["expected_label"],
                "score": domain_result["score"] if domain_result else None,
            }
        )
    passed = sum(1 for row in rows if row["passed"])
    return {
        "ok": True,
        "domain_id": domain_id,
        "total": len(rows),
        "passed": passed,
        "accuracy": round(passed / len(rows), 3) if rows else 0,
        "rows": rows,
        "evaluated_at": utc_now(),
    }


def _find(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise HTTPException(status_code=404, detail=f"{label} not found: {item_id}")


def _sub_benchmark_summary(state: dict[str, Any]) -> list[dict[str, Any]]:
    feedback = state.get("feedback", [])
    benchmarks = state.get("benchmark_items", [])
    versions = state.get("agent_versions", {})
    rows = []
    for domain in state.get("risk_domains", []):
        domain_id = domain["id"]
        domain_items = [item for item in benchmarks if item.get("domain_id") == domain_id]
        domain_feedback = [
            item for item in feedback
            if item.get("domain_id") == domain_id or item.get("benchmark_domain_id") == domain_id
        ]
        rows.append(
            {
                "domain_id": domain_id,
                "domain_name": domain["name"],
                "subagent_id": domain["subagent_id"],
                "agent_version": versions.get(domain_id, 1),
                "benchmark_count": len(domain_items),
                "feedback_count": len(domain_feedback),
                "badcase_count": sum(1 for item in domain_items if item.get("source") == "feedback"),
                "latest_badcase_at": next((item.get("created_at") for item in domain_items if item.get("source") == "feedback"), ""),
            }
        )
    return rows
