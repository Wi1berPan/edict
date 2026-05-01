from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .provider import ProviderError, analyze_with_doubao


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", text or "")}


@dataclass(frozen=True)
class RiskSubAgent:
    """A deterministic local subagent used for development and benchmark replay."""

    domain: dict[str, Any]
    version: int
    feedback_memory: list[dict[str, Any]]

    def analyze(self, video: dict[str, Any]) -> dict[str, Any]:
        text_parts = [
            video.get("title", ""),
            video.get("description", ""),
            video.get("transcript", ""),
            " ".join(video.get("ocr_texts", [])),
            " ".join(label.get("label", "") for label in video.get("grounding", [])),
        ]
        corpus = "\n".join(text_parts)
        corpus_lower = corpus.lower()
        domain_keywords = [kw.lower() for kw in self.domain.get("keywords", [])]
        learned_keywords = [
            kw.lower()
            for item in self.feedback_memory
            if item.get("domain_id") == self.domain["id"]
            for kw in item.get("learned_keywords", [])
        ]
        keywords = sorted(set(domain_keywords + learned_keywords))
        matched = [kw for kw in keywords if kw and kw in corpus_lower]

        grounding_hits = [
            g for g in video.get("grounding", [])
            if any(kw in str(g.get("label", "")).lower() for kw in keywords)
        ]
        ocr_hits = [
            text for text in video.get("ocr_texts", [])
            if any(kw in text.lower() for kw in keywords)
        ]
        transcript_hits = _extract_snippets(video.get("transcript", ""), matched)

        score = min(1.0, 0.18 * len(matched) + 0.12 * len(grounding_hits) + 0.08 * len(ocr_hits))
        threshold = float(self.domain.get("threshold", 0.5))
        verdict = "violation" if score >= threshold else "pass"
        confidence = min(0.98, 0.55 + abs(score - threshold))

        reasoning = [
            {
                "step": "collect_signals",
                "summary": "Collected transcript, OCR text, and grounding labels for the domain.",
                "inputs": {
                    "transcript_chars": len(video.get("transcript", "")),
                    "ocr_items": len(video.get("ocr_texts", [])),
                    "grounding_items": len(video.get("grounding", [])),
                },
            },
            {
                "step": "policy_match",
                "summary": "Matched policy keywords and feedback-learned keywords.",
                "matched_keywords": matched,
                "learned_keyword_count": len(learned_keywords),
            },
            {
                "step": "score",
                "summary": "Computed a transparent local risk score for benchmarkable development.",
                "score": round(score, 3),
                "threshold": threshold,
                "verdict": verdict,
                "confidence": round(confidence, 3),
            },
        ]

        return {
            "domain_id": self.domain["id"],
            "domain_name": self.domain["name"],
            "subagent_id": self.domain["subagent_id"],
            "agent_version": self.version,
            "verdict": verdict,
            "score": round(score, 3),
            "confidence": round(confidence, 3),
            "matched_keywords": matched,
            "evidence": {
                "grounding": grounding_hits,
                "ocr": ocr_hits,
                "transcript": transcript_hits,
            },
            "reasoning": reasoning,
        }


class VideoModerationOrchestrator:
    def __init__(self, state: dict[str, Any]):
        self.state = state

    def analyze(self, video: dict[str, Any]) -> dict[str, Any]:
        domain_results = []
        for domain in self.state["risk_domains"]:
            version = self.state.get("agent_versions", {}).get(domain["id"], 1)
            agent = RiskSubAgent(domain, version, self.state.get("feedback", []))
            domain_results.append(agent.analyze(video))

        violations = [item for item in domain_results if item["verdict"] == "violation"]
        max_score = max((item["score"] for item in domain_results), default=0.0)
        final_verdict = "violation" if violations else "pass"

        return {
            "video_id": video["id"],
            "final_verdict": final_verdict,
            "risk_score": round(max_score, 3),
            "domain_results": domain_results,
            "trace": {
                "pipeline": [
                    "video_intake",
                    "frame_grounding",
                    "ocr_extraction",
                    "risk_domain_subagents",
                    "final_aggregation",
                ],
                "grounding": video.get("grounding", []),
                "ocr": video.get("ocr_texts", []),
                "transcript": video.get("transcript", ""),
            },
            "created_at": utc_now(),
        }

    async def analyze_async(self, video: dict[str, Any], provider: str = "local") -> dict[str, Any]:
        if provider == "ark":
            provider = "doubao"
        if provider != "doubao":
            return self.analyze(video)

        domain_results = []
        fallback_errors = []
        for domain in self.state["risk_domains"]:
            version = self.state.get("agent_versions", {}).get(domain["id"], 1)
            try:
                result = await analyze_with_doubao(domain, video)
                result = _normalize_provider_result(result, domain, version)
            except ProviderError as exc:
                agent = RiskSubAgent(domain, version, self.state.get("feedback", []))
                result = agent.analyze(video)
                result["provider"] = "local_fallback"
                result["provider_error"] = str(exc)
                fallback_errors.append({"domain_id": domain["id"], "error": str(exc)})
            domain_results.append(result)

        violations = [item for item in domain_results if item["verdict"] == "violation"]
        max_score = max((item["score"] for item in domain_results), default=0.0)
        return {
            "video_id": video["id"],
            "final_verdict": "violation" if violations else "pass",
            "risk_score": round(max_score, 3),
            "domain_results": domain_results,
            "trace": {
                "pipeline": [
                    "video_intake",
                    "real_video_or_frame_input",
                    "doubao_ark_subagents",
                    "final_aggregation",
                ],
                "provider": "doubao",
                "fallback_errors": fallback_errors,
                "grounding": video.get("grounding", []),
                "ocr": video.get("ocr_texts", []),
                "transcript": video.get("transcript", ""),
                "source_url": video.get("source_url", ""),
                "file_name": video.get("file_name", ""),
            },
            "created_at": utc_now(),
        }

    def learn_from_feedback(self, feedback: dict[str, Any]) -> dict[str, Any]:
        domain_id = feedback["domain_id"]
        correction = feedback.get("correct_label", "")
        notes = feedback.get("notes", "")
        learned_keywords = sorted(_tokens(notes) - {"pass", "violation", "safe", "unsafe"})
        feedback["learned_keywords"] = learned_keywords[:12]

        self.state.setdefault("agent_versions", {})
        self.state["agent_versions"][domain_id] = self.state["agent_versions"].get(domain_id, 1) + 1
        return {
            "domain_id": domain_id,
            "new_version": self.state["agent_versions"][domain_id],
            "learned_keywords": feedback["learned_keywords"],
            "correction": correction,
        }

    def rebuild_subagents(self, domain_id: str | None = None, reason: str = "manual") -> dict[str, Any]:
        """Recompute agent versions from feedback and benchmark state.

        This is deliberately deterministic for local development: each rebuild creates
        an auditable version bump and refresh event for every selected subagent.
        """

        selected_domains = [
            domain for domain in self.state["risk_domains"]
            if domain_id is None or domain["id"] == domain_id
        ]
        self.state.setdefault("agent_versions", {})
        self.state.setdefault("iteration_log", [])

        updates = []
        for domain in selected_domains:
            did = domain["id"]
            domain_feedback = [item for item in self.state.get("feedback", []) if item.get("domain_id") == did]
            domain_benchmarks = [item for item in self.state.get("benchmark_items", []) if item.get("domain_id") == did]
            old_version = self.state["agent_versions"].get(did, 1)
            new_version = old_version + 1
            self.state["agent_versions"][did] = new_version
            learned_keywords = sorted({
                keyword
                for item in domain_feedback
                for keyword in item.get("learned_keywords", [])
            })
            update = {
                "domain_id": did,
                "subagent_id": domain["subagent_id"],
                "old_version": old_version,
                "new_version": new_version,
                "feedback_count": len(domain_feedback),
                "benchmark_count": len(domain_benchmarks),
                "learned_keywords": learned_keywords[:30],
                "reason": reason,
                "created_at": utc_now(),
            }
            self.state["iteration_log"].insert(0, update)
            updates.append(update)

        return {"updated": updates, "count": len(updates)}


def _extract_snippets(text: str, keywords: list[str]) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    lower = text.lower()
    for keyword in keywords[:8]:
        pos = lower.find(keyword)
        if pos >= 0:
            start = max(0, pos - 36)
            end = min(len(text), pos + len(keyword) + 36)
            snippets.append({"keyword": keyword, "snippet": text[start:end]})
    return snippets


def _normalize_provider_result(result: dict[str, Any], domain: dict[str, Any], version: int) -> dict[str, Any]:
    verdict = result.get("verdict") if result.get("verdict") in {"pass", "violation"} else "pass"
    score = _clamp_float(result.get("score"), 0.0, 1.0)
    confidence = _clamp_float(result.get("confidence"), 0.0, 1.0)
    return {
        "domain_id": domain["id"],
        "domain_name": domain["name"],
        "subagent_id": domain["subagent_id"],
        "agent_version": version,
        "provider": result.get("provider", "doubao"),
        "model": result.get("model"),
        "verdict": verdict,
        "score": round(score, 3),
        "confidence": round(confidence, 3),
        "matched_keywords": list(result.get("matched_keywords") or []),
        "evidence": result.get("evidence") or {"grounding": [], "ocr": [], "transcript": []},
        "reasoning": list(result.get("reasoning") or []),
    }


def _clamp_float(value: Any, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))
